import json
import re
import sqlite3
import time
from datetime import date, datetime, timedelta
from html import escape
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import quote

import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="Companies House Shared Shareholder Screener", layout="wide")

BASE_URL = "https://api.company-information.service.gov.uk"
DB_PATH = "companies_house_shared_shareholder.db"
SEARCH_PAGE_SIZE = 5000
PSC_PAGE_SIZE = 100
COMPANY_SEARCH_PAGE_SIZE = 100
MAX_SEARCH_RESULTS_PER_PSC = 30  # Cap to avoid huge fan-out

ALLOWED_COMPANY_TYPES = [
    "ltd",
    "llp",
    "private-limited-guarant-nsc",
    "private-limited-shares-section-30-exemption",
]


def normalize_name(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value).strip()).upper()


def make_company_profile_url(company_number: str, company_name: str = "") -> str:
    return f"https://find-and-update.company-information.service.gov.uk/company/{company_number}#{quote(company_name or 'company')}"


def utc_now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")


class CHClient:
    def __init__(self, api_keys: List[str]):
        self.api_keys = [key.strip() for key in api_keys if str(key).strip()]
        if not self.api_keys:
            raise ValueError("No Companies House API keys supplied.")
        self.key_index = 0
        self.session = requests.Session()

    def _auth(self) -> Tuple[str, str]:
        return self.api_keys[self.key_index % len(self.api_keys)], ""

    def _rotate_key(self) -> None:
        self.key_index = (self.key_index + 1) % len(self.api_keys)

    def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        last_error = None
        for _ in range(max(len(self.api_keys) * 3, 3)):
            try:
                response = self.session.get(
                    f"{BASE_URL}{path}",
                    params=params,
                    auth=self._auth(),
                    timeout=30,
                    headers={"Accept": "application/json"},
                )
                if response.status_code == 404:
                    return {}
                if response.status_code in (401, 403, 429):
                    last_error = f"HTTP {response.status_code}"
                    self._rotate_key()
                    time.sleep(0.5)
                    continue
                response.raise_for_status()
                return response.json()
            except requests.RequestException as exc:
                last_error = str(exc)
                self._rotate_key()
                time.sleep(0.5)
        raise RuntimeError(f"Companies House API request failed after retries: {last_error}")


def paged_get_items(
    client: CHClient,
    path: str,
    page_size: int,
    extra_params: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    start_index = 0
    while True:
        params = {"start_index": start_index}
        if extra_params:
            params.update(extra_params)
        if path == "/advanced-search/companies":
            params["size"] = page_size
        else:
            params["items_per_page"] = page_size
        payload = client.get(path, params=params)
        batch = payload.get("items", []) or []
        items.extend(batch)
        total = payload.get("total_results")
        if total is None:
            total = payload.get("total_count")
        total = int(total or len(items))
        start_index += page_size
        if not batch or start_index >= total:
            break
    return items


def validate_api_keys() -> List[str]:
    if "COMPANIES_HOUSE_API_KEYS" not in st.secrets:
        raise ValueError("Missing COMPANIES_HOUSE_API_KEYS in .streamlit/secrets.toml")
    keys = [str(key).strip() for key in list(st.secrets["COMPANIES_HOUSE_API_KEYS"]) if str(key).strip()]
    if not keys:
        raise ValueError("COMPANIES_HOUSE_API_KEYS is empty")
    return keys


def init_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS review_history (
            company_number TEXT PRIMARY KEY,
            incorporation_date TEXT,
            first_reviewed_at TEXT NOT NULL,
            last_reviewed_at TEXT NOT NULL,
            review_count INTEGER NOT NULL DEFAULT 1,
            last_error TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS candidate_companies (
            company_number TEXT PRIMARY KEY,
            company_name TEXT NOT NULL,
            incorporation_date TEXT,
            company_type TEXT,
            sic_codes TEXT,
            profile_url TEXT,
            first_screened_at TEXT NOT NULL,
            last_screened_at TEXT NOT NULL,
            screening_count INTEGER NOT NULL DEFAULT 1,
            shortlisted INTEGER NOT NULL DEFAULT 0,
            raw_json TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS company_pscs (
            company_number TEXT NOT NULL,
            shareholder_name TEXT NOT NULL,
            shareholder_key TEXT NOT NULL,
            psc_kind TEXT,
            psc_link TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (company_number, shareholder_key, psc_kind)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS shareholder_company_cache (
            shareholder_key TEXT NOT NULL,
            shareholder_name TEXT NOT NULL,
            company_number TEXT NOT NULL,
            company_name TEXT NOT NULL,
            company_status TEXT,
            profile_url TEXT NOT NULL,
            cached_at TEXT NOT NULL,
            PRIMARY KEY (shareholder_key, company_number)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS shareholder_search_cache (
            shareholder_key TEXT PRIMARY KEY,
            shareholder_name TEXT NOT NULL,
            search_done_at TEXT NOT NULL,
            search_count INTEGER NOT NULL DEFAULT 1
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_review_history_date ON review_history(incorporation_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_company_pscs_shareholder ON company_pscs(shareholder_key)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_shareholder_cache_key ON shareholder_company_cache(shareholder_key)")
    conn.commit()
    return conn


def reviewed_company_numbers(conn: sqlite3.Connection) -> Set[str]:
    rows = conn.execute("SELECT company_number FROM review_history").fetchall()
    return {row[0] for row in rows}


def record_review(conn: sqlite3.Connection, company_number: str, incorporation_date: str, error: str = "") -> None:
    now = utc_now()
    conn.execute(
        """
        INSERT INTO review_history (
            company_number, incorporation_date, first_reviewed_at, last_reviewed_at, review_count, last_error
        ) VALUES (?, ?, ?, ?, 1, ?)
        ON CONFLICT(company_number) DO UPDATE SET
            incorporation_date = excluded.incorporation_date,
            last_reviewed_at = excluded.last_reviewed_at,
            review_count = review_history.review_count + 1,
            last_error = excluded.last_error
        """,
        (company_number, incorporation_date, now, now, error),
    )
    conn.commit()


def upsert_candidate_company(conn: sqlite3.Connection, row: Dict[str, Any]) -> None:
    now = utc_now()
    conn.execute(
        """
        INSERT INTO candidate_companies (
            company_number, company_name, incorporation_date, company_type, sic_codes,
            profile_url, first_screened_at, last_screened_at, screening_count, shortlisted, raw_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 0, ?)
        ON CONFLICT(company_number) DO UPDATE SET
            company_name = excluded.company_name,
            incorporation_date = excluded.incorporation_date,
            company_type = excluded.company_type,
            sic_codes = excluded.sic_codes,
            profile_url = excluded.profile_url,
            last_screened_at = excluded.last_screened_at,
            screening_count = candidate_companies.screening_count + 1,
            raw_json = excluded.raw_json
        """,
        (
            row["company_number"], row["company_name"], row["incorporation_date"], row["company_type"],
            row["sic_codes"], row["profile_url"], now, now, json.dumps(row["raw_json"]),
        ),
    )
    conn.commit()


def replace_company_pscs(conn: sqlite3.Connection, company_number: str, pscs: List[Dict[str, str]]) -> None:
    now = utc_now()
    conn.execute("DELETE FROM company_pscs WHERE company_number = ?", (company_number,))
    conn.executemany(
        """
        INSERT OR REPLACE INTO company_pscs (
            company_number, shareholder_name, shareholder_key, psc_kind, psc_link, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            (
                company_number,
                psc["shareholder_name"],
                psc["shareholder_key"],
                psc["psc_kind"],
                psc["psc_link"],
                now,
            )
            for psc in pscs
        ],
    )
    conn.commit()


def set_shortlisted_state(conn: sqlite3.Connection, company_number: str, shortlisted: bool) -> None:
    conn.execute(
        "UPDATE candidate_companies SET shortlisted = ? WHERE company_number = ?",
        (int(shortlisted), company_number),
    )
    conn.commit()


def search_new_companies(client: CHClient, start_date: str, end_date: str) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    params = {
        "incorporated_from": start_date,
        "incorporated_to": end_date,
        "company_status": "active",
        "company_type": ",".join(ALLOWED_COMPANY_TYPES),
    }
    items = paged_get_items(client, "/advanced-search/companies", SEARCH_PAGE_SIZE, params)
    allowed_types = {value.lower() for value in ALLOWED_COMPANY_TYPES}
    candidates: Dict[str, Dict[str, Any]] = {}
    for item in items:
        company_number = str(item.get("company_number") or "")
        incorporation_date = str(item.get("date_of_creation") or item.get("incorporation_date") or "")
        if not company_number or not incorporation_date:
            continue
        if not start_date <= incorporation_date <= end_date:
            continue
        if str(item.get("company_status") or "").lower() != "active":
            continue
        if str(item.get("company_type") or "").lower() not in allowed_types:
            continue
        candidates[company_number] = item
    return list(candidates.values()), {"raw_results": len(items), "candidate_results": len(candidates)}


def get_all_pscs(client: CHClient, company_number: str) -> List[Dict[str, Any]]:
    return paged_get_items(client, f"/company/{company_number}/persons-with-significant-control", PSC_PAGE_SIZE)


def extract_named_pscs(pscs: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    result: List[Dict[str, str]] = []
    seen: Set[Tuple[str, str]] = set()
    for psc in pscs:
        name = str(psc.get("name") or "").strip()
        kind = str(psc.get("kind") or "")
        key = normalize_name(name)
        if not key:
            continue
        dedupe_key = (key, kind)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        result.append(
            {
                "shareholder_name": name,
                "shareholder_key": key,
                "psc_kind": kind,
                "psc_link": str((psc.get("links") or {}).get("self") or ""),
            }
        )
    return result


def company_search_for_shareholder(
    client: CHClient,
    shareholder_name: str,
    shareholder_key: str,
    max_results: int = MAX_SEARCH_RESULTS_PER_PSC,
) -> List[Dict[str, str]]:
    search_items = paged_get_items(
        client,
        "/search/companies",
        COMPANY_SEARCH_PAGE_SIZE,
        {"q": shareholder_name},
    )
    matches: List[Dict[str, str]] = []
    checked = 0
    for company in search_items:
        if checked >= max_results:
            break
        company_number = str(company.get("company_number") or "")
        company_name = str(company.get("title") or company.get("company_name") or "")
        if not company_number:
            continue
        try:
            pscs = get_all_pscs(client, company_number)
        except Exception:
            continue
        exact_psc_match = any(normalize_name(psc.get("name")) == shareholder_key for psc in pscs)
        if exact_psc_match:
            matches.append(
                {
                    "company_number": company_number,
                    "company_name": company_name,
                    "company_status": str(company.get("company_status") or ""),
                    "profile_url": make_company_profile_url(company_number, company_name),
                }
            )
        checked += 1
    return matches


def cache_shareholder_companies(
    conn: sqlite3.Connection,
    shareholder_name: str,
    shareholder_key: str,
    companies: List[Dict[str, str]],
) -> None:
    now = utc_now()
    conn.execute("DELETE FROM shareholder_company_cache WHERE shareholder_key = ?", (shareholder_key,))
    conn.executemany(
        """
        INSERT INTO shareholder_company_cache (
            shareholder_key, shareholder_name, company_number, company_name,
            company_status, profile_url, cached_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                shareholder_key,
                shareholder_name,
                company["company_number"],
                company["company_name"],
                company["company_status"],
                company["profile_url"],
                now,
            )
            for company in companies
        ],
    )
    conn.commit()


def mark_shareholder_searched(conn: sqlite3.Connection, shareholder_name: str, shareholder_key: str) -> None:
    now = utc_now()
    conn.execute(
        """
        INSERT INTO shareholder_search_cache (shareholder_key, shareholder_name, search_done_at, search_count)
        VALUES (?, ?, ?, 1)
        ON CONFLICT(shareholder_key) DO UPDATE SET
            search_done_at = excluded.search_done_at,
            search_count = shareholder_search_cache.search_count + 1
        """,
        (shareholder_key, shareholder_name, now),
    )
    conn.commit()


def cached_additional_companies(conn: sqlite3.Connection, shareholder_key: str, current_company_number: str) -> List[Dict[str, str]]:
    rows = conn.execute(
        """
        SELECT company_number, company_name, company_status, profile_url
        FROM shareholder_company_cache
        WHERE shareholder_key = ? AND company_number != ?
        ORDER BY company_name
        """,
        (shareholder_key, current_company_number),
    ).fetchall()
    return [
        {
            "company_number": row[0],
            "company_name": row[1],
            "company_status": row[2] or "",
            "profile_url": row[3],
        }
        for row in rows
    ]


def build_additional_companies_html(companies: List[Dict[str, str]]) -> str:
    if not companies:
        return ""
    links = []
    for company in companies:
        label = escape(f"{company['company_name']} ({company['company_number']})")
        url = escape(company["profile_url"], quote=True)
        links.append(f'<a href="{url}" target="_blank">{label}</a>')
    return "<br>".join(links)


def read_qualifying_rows(conn: sqlite3.Connection, start_date: str, end_date: str) -> pd.DataFrame:
    return pd.read_sql_query(
        """
        SELECT
            c.company_number,
            c.company_name,
            c.incorporation_date,
            c.company_type,
            c.sic_codes,
            c.profile_url,
            c.first_screened_at,
            c.last_screened_at,
            c.screening_count,
            c.shortlisted,
            p.shareholder_name,
            p.shareholder_key
        FROM candidate_companies c
        JOIN company_pscs p ON p.company_number = c.company_number
        WHERE c.incorporation_date BETWEEN ? AND ?
        ORDER BY c.incorporation_date DESC, c.company_name, p.shareholder_name
        """,
        conn,
        params=(start_date, end_date),
    )


def build_display_df(conn: sqlite3.Connection, db_df: pd.DataFrame) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    for _, row in db_df.iterrows():
        additional = cached_additional_companies(conn, row["shareholder_key"], row["company_number"])
        if not additional:
            continue
        rows.append(
            {
                "Shortlist": bool(row["shortlisted"]),
                "Company Name": row["company_name"],
                "Company Number": row["company_number"],
                "Incorporation Date": row["incorporation_date"],
                "Company Type": row["company_type"],
                "SIC Codes": row["sic_codes"] or "",
                "Shared Shareholder": row["shareholder_name"],
                "Additional Company Count": len(additional),
                "Additional Companies": build_additional_companies_html(additional),
                "Profile": row["profile_url"],
                "First Screened": row["first_screened_at"],
                "Last Screened": row["last_screened_at"],
                "Screening Count": int(row["screening_count"]),
                "company_number": row["company_number"],
            }
        )
    return pd.DataFrame(rows)


def render_sidebar(default_start: date, default_end: date) -> Tuple[date, date, bool, bool, str, bool]:
    with st.sidebar:
        st.header("Screening controls")
        start_date = st.date_input("Incorporated from", value=default_start, format="YYYY-MM-DD")
        end_date = st.date_input("Incorporated to", value=default_end, format="YYYY-MM-DD")
        rescreen = st.checkbox(
            "Re-screen companies already reviewed",
            value=False,
            help="Normally leave this off. The app remembers each PSC review and only processes new Companies House results.",
        )
        run = st.button("Review date range", type="primary", use_container_width=True)
        st.divider()
        company_name_search = st.text_input("Filter by company name", placeholder="e.g. Labs")
        shortlisted_only = st.checkbox("Show shortlisted only", value=False)
        st.caption("No SIC filters are used. Shareholders are matched on exact name after case and whitespace normalisation.")
    return start_date, end_date, rescreen, run, company_name_search, shortlisted_only


def main() -> None:
    st.title("Companies House Shared Shareholder Screener")
    st.caption("Find newly incorporated UK companies whose named PSC/shareholder also appears as a PSC on another UK company.")
    st.info("No SIC code filters are applied. A shareholder match uses the exact Companies House PSC name, ignoring only case and repeated whitespace.")

    try:
        api_keys = validate_api_keys()
    except Exception as exc:
        st.error(str(exc))
        st.stop()

    conn = init_db()
    client = CHClient(api_keys)
    default_end = date.today()
    default_start = default_end - timedelta(days=30)
    start_date, end_date, rescreen, run, company_name_search, shortlisted_only = render_sidebar(default_start, default_end)

    if start_date > end_date:
        st.error("The start date must be on or before the end date.")
        st.stop()

    start_str, end_str = start_date.isoformat(), end_date.isoformat()

    if run:
        failures: List[str] = []
        with st.status("Reviewing companies and shareholder links...", expanded=True) as status:
            candidates, diagnostics = search_new_companies(client, start_str, end_str)
            reviewed = reviewed_company_numbers(conn)
            to_review = candidates if rescreen else [item for item in candidates if str(item.get("company_number") or "") not in reviewed]
            st.write(f"Raw Companies House results: {diagnostics['raw_results']:,}")
            st.write(f"Date-range candidates: {diagnostics['candidate_results']:,}")
            st.write(f"Previously reviewed companies skipped: {len(candidates) - len(to_review):,}")
            st.write(f"Companies requiring PSC review now: {len(to_review):,}")

            progress = st.progress(0)
            total = max(len(to_review), 1)
            processed = 0
            psc_searches_run = 0
            psc_with_additional = 0

            for index, item in enumerate(to_review, start=1):
                company_number = str(item.get("company_number") or "unknown")
                incorporation_date = str(item.get("date_of_creation") or item.get("incorporation_date") or "")
                try:
                    company_name = str(item.get("company_name") or item.get("title") or "")
                    candidate_row = {
                        "company_number": company_number,
                        "company_name": company_name,
                        "incorporation_date": incorporation_date,
                        "company_type": str(item.get("company_type") or ""),
                        "sic_codes": ", ".join(str(code) for code in (item.get("sic_codes") or [])),
                        "profile_url": make_company_profile_url(company_number, company_name),
                        "raw_json": item,
                    }
                    pscs = extract_named_pscs(get_all_pscs(client, company_number))
                    upsert_candidate_company(conn, candidate_row)
                    replace_company_pscs(conn, company_number, pscs)

                    for psc in pscs:
                        key = psc["shareholder_key"]
                        existing = conn.execute(
                            "SELECT search_done_at FROM shareholder_search_cache WHERE shareholder_key = ?", (key,)
                        ).fetchone()
                        if existing is None:
                            related = company_search_for_shareholder(client, psc["shareholder_name"], key)
                            cache_shareholder_companies(conn, psc["shareholder_name"], key, related)
                            mark_shareholder_searched(conn, psc["shareholder_name"], key)
                            psc_searches_run += 1
                            if any(company["company_number"] != company_number for company in related):
                                psc_with_additional += 1
                        else:
                            pass

                    record_review(conn, company_number, incorporation_date)
                    processed += 1
                except Exception as exc:
                    record_review(conn, company_number, incorporation_date, error=str(exc))
                    failures.append(f"{company_number}: {exc}")
                progress.progress(min(index / total, 1.0))

            st.write(f"Companies reviewed: {processed:,}")
            st.write(f"Distinct PSC searches performed this run: {psc_searches_run:,}")
            st.write(f"PSC names with at least one additional company found: {psc_with_additional:,}")
            if failures:
                st.warning(f"Failed reviews: {len(failures):,}")
                st.code("\n".join(failures[:50]))
                status.update(label="Completed with some errors", state="error")
            else:
                status.update(label="Review complete", state="complete")

    db_df = read_qualifying_rows(conn, start_str, end_str)
    display_df = build_display_df(conn, db_df)
    if display_df.empty:
        display_df = pd.DataFrame(columns=["Shortlist", "Company Name", "Company Number", "Incorporation Date", "Company Type", "SIC Codes", "Shared Shareholder", "Additional Company Count", "Additional Companies", "Profile", "First Screened", "Last Screened", "Screening Count", "company_number"])

    filtered_df = display_df.copy()
    if shortlisted_only:
        filtered_df = filtered_df[filtered_df["Shortlist"]].copy()
    if company_name_search.strip():
        filtered_df = filtered_df[filtered_df["Company Name"].astype(str).str.contains(re.escape(company_name_search.strip()), case=False, na=False)].copy()

    c1, c2, c3 = st.columns(3)
    c1.metric("Shared-shareholder Rows", f"{len(filtered_df):,}")
    c2.metric("New Companies", f"{filtered_df['Company Number'].nunique() if not filtered_df.empty else 0:,}")
    c3.metric("Shortlisted", f"{int(filtered_df['Shortlist'].sum()) if not filtered_df.empty else 0:,}")

    st.subheader("New companies with a shared shareholder")
    st.caption(f"Showing {len(filtered_df):,} shareholder-level matches for companies incorporated from {start_str} to {end_str}. Each additional-company link opens the Companies House profile in a new tab.")

    edited_df = st.data_editor(
        filtered_df,
        use_container_width=True,
        hide_index=True,
        disabled=[column for column in filtered_df.columns if column not in {"Shortlist"}],
        column_config={
            "Shortlist": st.column_config.CheckboxColumn("Shortlist"),
            "Profile": st.column_config.LinkColumn("New Company Profile", display_text="Open record"),
            "Additional Companies": st.column_config.TextColumn("Additional Companies", width="large", help="HTML links are rendered in the detail cards below."),
            "company_number": None,
        },
        key=f"results_editor_{start_str}_{end_str}",
    )

    if not edited_df.empty:
        changes = edited_df[["company_number", "Shortlist"]].drop_duplicates().merge(
            display_df[["company_number", "Shortlist"]].drop_duplicates(),
            on="company_number",
            suffixes=("_new", "_old"),
            how="left",
        )
        changed = changes[changes["Shortlist_new"] != changes["Shortlist_old"]]
        for _, row in changed.iterrows():
            set_shortlisted_state(conn, row["company_number"], bool(row["Shortlist_new"]))
        if not changed.empty:
            st.success(f"Updated shortlist state for {len(changed):,} compan{'y' if len(changed) == 1 else 'ies'}.")
            st.rerun()

    st.subheader("Additional company links")
    if filtered_df.empty:
        st.info("Run a date-range review to find shareholder matches.")
    else:
        for _, row in filtered_df.iterrows():
            with st.expander(f"{row['Company Name']} — {row['Shared Shareholder']} ({row['Additional Company Count']} additional companies)"):
                st.markdown(f"**New company:** [{row['Company Name']} ({row['Company Number']})]({row['Profile']})")
                st.markdown(f"**Shared shareholder:** {row['Shared Shareholder']}")
                st.markdown(f"**Additional Companies House companies:**<br>{row['Additional Companies']}", unsafe_allow_html=True)

    csv_df = filtered_df.copy()
    csv_df["Additional Companies"] = csv_df["Additional Companies"].str.replace(r"<br>", " | ", regex=True).str.replace(r"<[^>]+>", "", regex=True)
    csv = csv_df.drop(columns=["company_number"], errors="ignore").to_csv(index=False).encode("utf-8")
    st.download_button("Download filtered CSV", data=csv, file_name=f"shared_shareholder_companies_{start_str}_to_{end_str}.csv", mime="text/csv", use_container_width=True)

    with st.expander("How matching and repeat screening work"):
        st.markdown("""
- The app fetches named PSCs for each newly incorporated company in the selected range.
- For each PSC name, it searches Companies House and verifies an exact PSC-name match against up to 30 companies to keep runtimes reasonable.
- The original company is shown only when at least one named PSC is verified on another company.
- `review_history` records every reviewed company, including no-match results, so normal repeat runs only process companies not previously screened.
- `shareholder_search_cache` remembers which PSC names have already been searched; subsequent runs reuse cached results unless you explicitly re-screen.
- Enable **Re-screen companies already reviewed** only when you intentionally want to refresh historical checks.
- SIC codes are shown only as reference data and are never used as filters.
        """)


if __name__ == "__main__":
    main()
