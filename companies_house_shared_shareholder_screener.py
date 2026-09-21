import json
import re
import sqlite3
import time
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import quote

import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="Companies House New Incorporations Screener", layout="wide")

BASE_URL = "https://api.company-information.service.gov.uk"
DB_PATH = "companies_house_screening.db"
SEARCH_PAGE_SIZE = 5000
OFFICERS_PAGE_SIZE = 100
PSC_PAGE_SIZE = 100

ALLOWED_SIC_CODES = [
    "62012", "62020", "63120", "47910", "46190", "46499", "70229", "73110", "74909", "68209",
    "64209", "68100", "32990", "10890", "86900", "93130", "96040", "82990", "72110", "56101",
]
TARGET_SIC_CODES = {"62012", "72110", "56101"}
MANUFACTURING_WHOLESALE_SIC_CODES = {
    "10110", "10130", "10310", "10410", "10511", "10512", "10611", "10612", "10840", "10850", "10890",
    "10920", "13100", "13200", "13300", "13921", "13923", "13960", "14131", "15110", "16290", "19200",
    "20110", "20120", "20130", "20140", "20150", "20160", "20170", "20200", "20301", "20302", "20411",
    "20412", "20590", "21100", "22210", "22290", "23190", "23910", "23990", "24100", "24200", "24310",
    "24320", "24330", "24340", "24410", "24420", "24430", "24440", "24450", "24460", "24510", "25110",
    "25210", "25500", "25990", "26110", "26200", "26300", "26511", "26512", "26600", "27110", "27200",
    "28110", "28290", "28300", "28990", "29100", "29310", "30110", "30300", "31090", "32990", "46110",
    "46120", "46130", "46140", "46150", "46160", "46170", "46180", "46190", "46210", "46220", "46230",
    "46240", "46310", "46320", "46330", "46341", "46342", "46350", "46360", "46370", "46380", "46390",
    "46410", "46420", "46431", "46439", "46440", "46450", "46460", "46470", "46480", "46499", "46510",
    "46520", "46530", "46610", "46620", "46630", "46640", "46650", "46660", "46690", "46711", "46719",
    "46720", "46730", "46740", "46750", "46900",
}
ALL_ALLOWED_SIC_CODES = list({*ALLOWED_SIC_CODES, *MANUFACTURING_WHOLESALE_SIC_CODES})

BONUS_STAR_COUNTRIES = {"sweden", "norway", "united states"}
ALLOWED_COMPANY_TYPES = [
    "ltd",
    "llp",
    "private-limited-guarant-nsc",
    "private-limited-shares-section-30-exemption",
]
COUNTRY_TERMS = {
    "usa", "united states", "united states of america", "france", "germany", "belgium", "norway",
    "sweden", "finland", "denmark", "austria", "poland", "spain", "portugal", "greece", "italy",
    "hungary", "croatia", "ireland", "china", "netherlands", "india", "hong kong", "singapore",
}
NATIONALITY_TO_COUNTRY = {
    "american": "united states", "us": "united states", "united states": "united states",
    "french": "france", "german": "germany", "belgian": "belgium", "norwegian": "norway",
    "swedish": "sweden", "finnish": "finland", "danish": "denmark", "austrian": "austria",
    "polish": "poland", "spanish": "spain", "portuguese": "portugal", "greek": "greece",
    "italian": "italy", "hungarian": "hungary", "croatian": "croatia", "irish": "ireland",
    "chinese": "china", "indian": "india", "hong kong": "hong kong", "hongkong": "hong kong",
    "singaporean": "singapore", "dutch": "netherlands", "netherlands": "netherlands",
}
COMPANY_OWNER_KINDS = {
    "corporate-entity-person-with-significant-control",
    "legal-person-person-with-significant-control",
    "super-secure-person-with-significant-control",
}
COUNTRY_FLAG_MAP = {
    "united states": "🇺🇸", "france": "🇫🇷", "germany": "🇩🇪", "belgium": "🇧🇪", "norway": "🇳🇴",
    "sweden": "🇸🇪", "finland": "🇫🇮", "denmark": "🇩🇰", "austria": "🇦🇹", "poland": "🇵🇱",
    "spain": "🇪🇸", "portugal": "🇵🇹", "greece": "🇬🇷", "italy": "🇮🇹", "hungary": "🇭🇺",
    "croatia": "🇭🇷", "ireland": "🇮🇪", "china": "🇨🇳", "netherlands": "🇳🇱", "india": "🇮🇳",
    "hong kong": "🇭🇰", "singapore": "🇸🇬",
}
SIGNAL_OPTIONS = ["International Director", "International Shareholder", "Owned By A Company", "Associated Companies"]


def apply_custom_css() -> None:
    st.markdown(
        """
        <style>
        [data-testid="stSidebar"][aria-expanded="true"] > div:first-child { width: 340px; }
        div[data-testid="metric-container"] {
            background: linear-gradient(180deg, rgba(14, 17, 23, 0.03), rgba(14, 17, 23, 0.01));
            border: 1px solid rgba(120, 120, 120, 0.18);
            padding: 14px 16px;
            border-radius: 14px;
        }
        .app-note {
            padding: 0.85rem 1rem;
            border-radius: 12px;
            border: 1px solid rgba(120, 120, 120, 0.18);
            background: rgba(49, 51, 63, 0.04);
            margin-bottom: 1rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip().lower().replace("-", " ")
    text = re.sub(r"[^a-z0-9\s]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    aliases = {
        "usa": "united states",
        "u s a": "united states",
        "united states of america": "united states",
        "the netherlands": "netherlands",
        "hongkong": "hong kong",
    }
    return aliases.get(text, text)


NORMALIZED_COUNTRY_TERMS = {normalize_text(x) for x in COUNTRY_TERMS}
NORMALIZED_ALLOWED_COMPANY_TYPES = {normalize_text(x) for x in ALLOWED_COMPANY_TYPES}


def canonical_country_from_value(value: Any) -> str:
    norm = normalize_text(value)
    if norm in NORMALIZED_COUNTRY_TERMS:
        return norm
    return NATIONALITY_TO_COUNTRY.get(norm, "")


def dedupe_preserve_order(values: List[str]) -> List[str]:
    output: List[str] = []
    seen: Set[str] = set()
    for value in values:
        key = normalize_text(value)
        if key and key not in seen:
            seen.add(key)
            output.append(value)
    return output


def country_label(value: str) -> str:
    if value == "united states":
        return "USA"
    if value == "hong kong":
        return "Hong Kong"
    return value.title()


def format_flagged_countries(values: List[str]) -> str:
    countries = dedupe_preserve_order(
        [canonical_country_from_value(value) for value in values if canonical_country_from_value(value)]
    )
    return " | ".join(f"✓ {COUNTRY_FLAG_MAP.get(country, '🌍')} {country_label(country)}" for country in countries)


def extract_country_flags(values: List[str]) -> List[str]:
    countries = dedupe_preserve_order(
        [canonical_country_from_value(value) for value in values if canonical_country_from_value(value)]
    )
    return [COUNTRY_FLAG_MAP[country] for country in countries if country in COUNTRY_FLAG_MAP]


def make_company_profile_url(company_number: str, company_name: str) -> str:
    return f"https://find-and-update.company-information.service.gov.uk/company/{company_number}#{quote(company_name or 'company')}"


class CHClient:
    def __init__(self, api_keys: List[str]):
        self.api_keys = [key.strip() for key in api_keys if str(key).strip()]
        if not self.api_keys:
            raise ValueError("No Companies House API keys supplied.")
        self.idx = 0
        self.session = requests.Session()

    def _auth(self) -> Tuple[str, str]:
        return self.api_keys[self.idx % len(self.api_keys)], ""

    def _rotate(self) -> None:
        self.idx = (self.idx + 1) % len(self.api_keys)

    def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        last_error: Optional[str] = None
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
                    self._rotate()
                    time.sleep(0.5)
                    continue
                response.raise_for_status()
                return response.json()
            except requests.RequestException as exc:
                last_error = str(exc)
                self._rotate()
                time.sleep(0.5)
        raise RuntimeError(f"Companies House API request failed after retries: {last_error}")


def ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        conn.commit()


def init_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS screened_companies (
            company_number TEXT PRIMARY KEY,
            company_name TEXT,
            sic_code TEXT,
            incorporation_date TEXT,
            company_type TEXT,
            international_director INTEGER,
            international_shareholder INTEGER,
            owned_by_company INTEGER,
            pulled_at TEXT,
            raw_json TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS psc_records (
            company_number TEXT NOT NULL,
            significant_owner_key TEXT NOT NULL,
            psc_id TEXT,
            psc_kind TEXT,
            psc_name TEXT,
            corporate_company_number TEXT,
            nationality TEXT,
            country_of_residence TEXT,
            notified_on TEXT,
            ceased_on TEXT,
            natures_of_control TEXT,
            source_json TEXT,
            PRIMARY KEY (company_number, significant_owner_key)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_psc_records_owner_key ON psc_records(significant_owner_key)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_psc_records_company_number ON psc_records(company_number)")
    conn.commit()

    ensure_column(conn, "screened_companies", "international_director_detail", "TEXT")
    ensure_column(conn, "screened_companies", "international_shareholder_detail", "TEXT")
    ensure_column(conn, "screened_companies", "owner_company_name", "TEXT")
    ensure_column(conn, "screened_companies", "profile_url", "TEXT")
    ensure_column(conn, "screened_companies", "shortlisted", "INTEGER DEFAULT 0")
    ensure_column(conn, "screened_companies", "target_sic", "INTEGER DEFAULT 0")
    ensure_column(conn, "screened_companies", "target_address", "INTEGER DEFAULT 0")
    ensure_column(conn, "screened_companies", "target_address_detail", "TEXT")
    ensure_column(conn, "screened_companies", "target_indicators", "TEXT")
    ensure_column(conn, "screened_companies", "associated_company_count", "INTEGER DEFAULT 0")
    ensure_column(conn, "screened_companies", "associated_companies", "TEXT")
    return conn


def existing_company_numbers(conn: sqlite3.Connection, incorporation_date: str) -> Set[str]:
    rows = conn.execute(
        "SELECT company_number FROM screened_companies WHERE incorporation_date = ?", (incorporation_date,)
    ).fetchall()
    return {row[0] for row in rows}


def set_shortlisted_state(conn: sqlite3.Connection, company_number: str, shortlisted: bool) -> None:
    conn.execute("UPDATE screened_companies SET shortlisted = ? WHERE company_number = ?", (int(shortlisted), company_number))
    conn.commit()


def upsert_company(conn: sqlite3.Connection, row: Dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO screened_companies (
            company_number, company_name, sic_code, incorporation_date, company_type,
            international_director, international_director_detail,
            international_shareholder, international_shareholder_detail,
            owned_by_company, owner_company_name, pulled_at, raw_json, profile_url,
            shortlisted, target_sic, target_address, target_address_detail, target_indicators,
            associated_company_count, associated_companies
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(company_number) DO UPDATE SET
            company_name=excluded.company_name,
            sic_code=excluded.sic_code,
            incorporation_date=excluded.incorporation_date,
            company_type=excluded.company_type,
            international_director=excluded.international_director,
            international_director_detail=excluded.international_director_detail,
            international_shareholder=excluded.international_shareholder,
            international_shareholder_detail=excluded.international_shareholder_detail,
            owned_by_company=excluded.owned_by_company,
            owner_company_name=excluded.owner_company_name,
            pulled_at=excluded.pulled_at,
            raw_json=excluded.raw_json,
            profile_url=excluded.profile_url,
            target_sic=excluded.target_sic,
            target_address=excluded.target_address,
            target_address_detail=excluded.target_address_detail,
            target_indicators=excluded.target_indicators,
            associated_company_count=excluded.associated_company_count,
            associated_companies=excluded.associated_companies
        """,
        (
            row["company_number"], row["company_name"], row["sic_code"], row["incorporation_date"],
            row["company_type"], int(row["international_director"]), row.get("international_director_detail", ""),
            int(row["international_shareholder"]), row.get("international_shareholder_detail", ""),
            int(row["owned_by_company"]), row.get("owner_company_name", ""), row["pulled_at"],
            json.dumps(row.get("raw_json", {})), row.get("profile_url", ""), int(row.get("shortlisted", False)),
            int(row.get("target_sic", False)), int(row.get("target_address", False)),
            row.get("target_address_detail", ""), row.get("target_indicators", ""),
            int(row.get("associated_company_count", 0)), row.get("associated_companies", ""),
        ),
    )
    conn.commit()


def read_db_rows(conn: sqlite3.Connection, incorporation_date: Optional[str] = None) -> pd.DataFrame:
    if incorporation_date:
        return pd.read_sql_query(
            "SELECT * FROM screened_companies WHERE incorporation_date = ? ORDER BY pulled_at DESC",
            conn,
            params=(incorporation_date,),
        )
    return pd.read_sql_query("SELECT * FROM screened_companies ORDER BY pulled_at DESC", conn)


def validate_api_keys() -> List[str]:
    if "COMPANIES_HOUSE_API_KEYS" not in st.secrets:
        raise ValueError("Missing COMPANIES_HOUSE_API_KEYS in .streamlit/secrets.toml")
    keys = [str(key).strip() for key in list(st.secrets["COMPANIES_HOUSE_API_KEYS"]) if str(key).strip()]
    if not keys:
        raise ValueError("COMPANIES_HOUSE_API_KEYS is empty")
    return keys


def paged_get_items(
    client: CHClient, path: str, page_size: int, extra_params: Optional[Dict[str, Any]] = None
) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    start_index = 0
    while True:
        params: Dict[str, Any] = {"start_index": start_index}
        if extra_params:
            params.update(extra_params)
        if path == "/advanced-search/companies":
            params["size"] = page_size
        else:
            params["items_per_page"] = page_size
        payload = client.get(path, params=params)
        batch = payload.get("items", []) or []
        items.extend(batch)
        total = int(payload.get("total_results") or payload.get("total_count") or len(items))
        start_index += page_size
        if not batch or start_index >= total:
            break
    return items


def is_allowed_company_type(value: Any) -> bool:
    return normalize_text(value) in NORMALIZED_ALLOWED_COMPANY_TYPES


def search_new_companies(client: CHClient, target_date: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    params = {
        "incorporated_from": target_date,
        "incorporated_to": target_date,
        "company_status": "active",
        "company_type": ",".join(ALLOWED_COMPANY_TYPES),
        "sic_codes": ",".join(ALL_ALLOWED_SIC_CODES),
    }
    items = paged_get_items(client, "/advanced-search/companies", SEARCH_PAGE_SIZE, params)
    filtered = [
        item for item in items
        if item.get("company_status", "").lower() == "active"
        and is_allowed_company_type(item.get("company_type", ""))
        and any(str(code) in ALL_ALLOWED_SIC_CODES for code in (item.get("sic_codes") or []))
    ]
    deduped = {item["company_number"]: item for item in filtered if item.get("company_number")}
    return list(deduped.values()), {
        "raw_results": len(items),
        "filtered_results": len(filtered),
        "deduped_results": len(deduped),
    }


def get_all_officers(client: CHClient, company_number: str) -> List[Dict[str, Any]]:
    return paged_get_items(client, f"/company/{company_number}/officers", OFFICERS_PAGE_SIZE)


def get_all_pscs(client: CHClient, company_number: str) -> List[Dict[str, Any]]:
    return paged_get_items(client, f"/company/{company_number}/persons-with-significant-control", PSC_PAGE_SIZE)


def extract_id_from_self_link(psc: Dict[str, Any]) -> str:
    self_link = str((psc.get("links") or {}).get("self") or "").rstrip("/")
    return self_link.split("/")[-1] if self_link else ""


def extract_corporate_company_number(psc: Dict[str, Any]) -> str:
    direct_values = [
        psc.get("company_number"),
        psc.get("identification", {}).get("registration_number"),
        psc.get("identification", {}).get("company_number"),
    ]
    for value in direct_values:
        if value:
            return str(value).strip().upper()

    links = psc.get("links") or {}
    for link_key in ("corporate_entity_beneficial_owner", "legal_person_beneficial_owner"):
        link = str(links.get(link_key) or "")
        match = re.search(r"/company/([^/]+)/", link)
        if match:
            return match.group(1).upper()
    return ""


def significant_owner_key(psc: Dict[str, Any]) -> str:
    """Return a stable Companies House PSC key; never use the owner name as the identity key."""
    psc_id = extract_id_from_self_link(psc)
    kind = str(psc.get("kind") or "unknown").strip().lower()
    corporate_number = extract_corporate_company_number(psc)

    if corporate_number:
        return f"corporate:{corporate_number}"
    if psc_id:
        return f"psc:{kind}:{psc_id}"

    # A missing API identity cannot safely be matched across companies.
    # This key is deliberately company-scoped, so it never creates a false cross-company match.
    return f"unmatched:{kind}:{json.dumps(psc, sort_keys=True, default=str)}"


def persist_psc_records(conn: sqlite3.Connection, company_number: str, pscs: List[Dict[str, Any]]) -> None:
    conn.execute("DELETE FROM psc_records WHERE company_number = ?", (company_number,))
    for psc in pscs:
        key = significant_owner_key(psc)
        conn.execute(
            """
            INSERT OR REPLACE INTO psc_records (
                company_number, significant_owner_key, psc_id, psc_kind, psc_name,
                corporate_company_number, nationality, country_of_residence, notified_on,
                ceased_on, natures_of_control, source_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                company_number,
                key,
                extract_id_from_self_link(psc),
                str(psc.get("kind") or ""),
                str(psc.get("name") or ""),
                extract_corporate_company_number(psc),
                str(psc.get("nationality") or ""),
                str(psc.get("country_of_residence") or ""),
                str(psc.get("notified_on") or ""),
                str(psc.get("ceased_on") or ""),
                json.dumps(psc.get("natures_of_control") or []),
                json.dumps(psc),
            ),
        )
    conn.commit()


def collect_international_director_details(client: CHClient, company_number: str) -> Tuple[bool, List[str], int]:
    matches: List[str] = []
    director_count = 0
    for officer in get_all_officers(client, company_number):
        role = normalize_text(officer.get("officer_role"))
        if "director" not in role and role != "designated member":
            continue
        director_count += 1
        for value in [
            officer.get("country_of_residence"),
            (officer.get("address") or {}).get("country"),
            officer.get("nationality"),
        ]:
            if canonical_country_from_value(value):
                matches.append(str(value))
    matches = dedupe_preserve_order(matches)
    return bool(matches), matches, director_count


def analyse_psc_flags(pscs: List[Dict[str, Any]]) -> Tuple[bool, List[str], bool, List[str]]:
    shareholder_matches: List[str] = []
    owner_names: List[str] = []
    for psc in pscs:
        if psc.get("ceased_on"):
            continue
        kind = str(psc.get("kind") or "")
        for value in [
            psc.get("country_of_residence"),
            (psc.get("address") or {}).get("country"),
            psc.get("nationality"),
        ]:
            if canonical_country_from_value(value):
                shareholder_matches.append(str(value))
        if kind in COMPANY_OWNER_KINDS or "corporate" in kind or "legal-person" in kind:
            name = str(psc.get("name") or "").strip()
            if name:
                owner_names.append(name)
    shareholder_matches = dedupe_preserve_order(shareholder_matches)
    owner_names = dedupe_preserve_order(owner_names)
    return bool(shareholder_matches), shareholder_matches, bool(owner_names), owner_names


def parse_matching_sic(item: Dict[str, Any]) -> str:
    codes = [str(code) for code in (item.get("sic_codes") or [])]
    matches = [code for code in codes if code in ALL_ALLOWED_SIC_CODES]
    return ", ".join(matches or codes[:1])


def is_target_sic(item: Dict[str, Any]) -> bool:
    return any(str(code) in TARGET_SIC_CODES for code in (item.get("sic_codes") or []))


def has_bonus_star(values: List[str]) -> bool:
    return bool({canonical_country_from_value(value) for value in values} & BONUS_STAR_COUNTRIES)


def is_target_address(item: Dict[str, Any]) -> Tuple[bool, str]:
    address = item.get("registered_office_address") or item.get("address") or {}
    country = canonical_country_from_value(address.get("country"))
    if not country:
        return False, ""
    return True, f"✓ {COUNTRY_FLAG_MAP.get(country, '🌍')} {country_label(country)}"


def build_target_indicators(
    target_sic: bool,
    target_address: bool,
    director_details: List[str],
    shareholder_details: List[str],
    director_count: int,
) -> str:
    output: List[str] = []
    if target_sic:
        output.append("🎯")
    if target_address:
        output.append("🏳️")
    output.extend(dedupe_preserve_order(extract_country_flags(director_details) + extract_country_flags(shareholder_details)))
    if director_count >= 2:
        output.append(f"{director_count} directors")
    return " ".join(output)


def build_rating(
    international_director: bool,
    international_shareholder: bool,
    owned_by_company: bool,
    target_sic: bool,
    director_details: List[str],
    shareholder_details: List[str],
) -> str:
    stars = sum([international_director, international_shareholder, owned_by_company, target_sic])
    if has_bonus_star(director_details) or has_bonus_star(shareholder_details):
        stars += 1
    return "⭐" * stars


def get_association_map(conn: sqlite3.Connection) -> Dict[str, List[Tuple[str, str, str]]]:
    """Return only current, shared significant-owner relationships.

    Matching is done exclusively with `significant_owner_key`: corporate registration
    numbers where known, otherwise the Companies House PSC resource identifier.
    Owner names are display-only and never determine an association.
    """
    rows = conn.execute(
        """
        SELECT
            p.company_number,
            p.significant_owner_key,
            COALESCE(NULLIF(p.psc_name, ''), p.significant_owner_key) AS owner_label,
            c.company_name
        FROM psc_records p
        JOIN screened_companies c ON c.company_number = p.company_number
        WHERE COALESCE(p.ceased_on, '') = ''
          AND p.significant_owner_key NOT LIKE 'unmatched:%'
        ORDER BY p.significant_owner_key, c.company_name
        """
    ).fetchall()

    by_key: Dict[str, List[Tuple[str, str, str]]] = {}
    for company_number, owner_key, owner_label, company_name in rows:
        by_key.setdefault(owner_key, []).append((company_number, company_name, owner_label))

    associations: Dict[str, List[Tuple[str, str, str]]] = {}
    for owner_key, members in by_key.items():
        unique_members = {member[0]: member for member in members}
        if len(unique_members) < 2:
            continue
        for company_number, company_name, owner_label in unique_members.values():
            for other_number, other_name, _ in unique_members.values():
                if other_number == company_number:
                    continue
                associations.setdefault(company_number, []).append((other_number, other_name, owner_label))

    for company_number, items in associations.items():
        deduped: Dict[str, Tuple[str, str, str]] = {}
        for other_number, other_name, owner_label in items:
            deduped[other_number] = (other_number, other_name, owner_label)
        associations[company_number] = sorted(deduped.values(), key=lambda item: item[1].lower())
    return associations


def refresh_association_fields(conn: sqlite3.Connection) -> None:
    associations = get_association_map(conn)
    all_company_numbers = [row[0] for row in conn.execute("SELECT company_number FROM screened_companies").fetchall()]
    for company_number in all_company_numbers:
        related = associations.get(company_number, [])
        display = " | ".join(f"{name} ({number})" for number, name, _ in related)
        conn.execute(
            """
            UPDATE screened_companies
            SET associated_company_count = ?, associated_companies = ?
            WHERE company_number = ?
            """,
            (len(related), display, company_number),
        )
    conn.commit()


def process_company(client: CHClient, conn: sqlite3.Connection, item: Dict[str, Any], target_date: str) -> Dict[str, Any]:
    company_number = str(item.get("company_number") or "")
    company_name = str(item.get("company_name") or item.get("title") or "")
    international_director, director_details, director_count = collect_international_director_details(client, company_number)
    pscs = get_all_pscs(client, company_number)
    persist_psc_records(conn, company_number, pscs)
    international_shareholder, shareholder_details, owned_by_company, owner_names = analyse_psc_flags(pscs)
    target_sic = is_target_sic(item)
    target_address, target_address_detail = is_target_address(item)

    return {
        "company_number": company_number,
        "company_name": company_name,
        "sic_code": parse_matching_sic(item),
        "incorporation_date": target_date,
        "company_type": item.get("company_type", ""),
        "international_director": international_director,
        "international_director_detail": format_flagged_countries(director_details),
        "international_shareholder": international_shareholder,
        "international_shareholder_detail": format_flagged_countries(shareholder_details),
        "owned_by_company": owned_by_company,
        "owner_company_name": " | ".join(f"✓ {name}" for name in owner_names),
        "pulled_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        "raw_json": item,
        "profile_url": make_company_profile_url(company_number, company_name),
        "shortlisted": False,
        "target_sic": target_sic,
        "target_address": target_address,
        "target_address_detail": target_address_detail,
        "target_indicators": build_target_indicators(
            target_sic, target_address, director_details, shareholder_details, director_count
        ),
        "associated_company_count": 0,
        "associated_companies": "",
    }


def build_display_df(db_df: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "Shortlist", "Target SIC", "Rating", "Target Indicators", "Company Name", "SIC Code", "Signals",
        "International Director", "International Shareholder", "Owned By A Company", "Associated Companies",
        "Associated Count", "Profile", "Pulled At", "company_number",
    ]
    if db_df.empty:
        return pd.DataFrame(columns=columns)

    signals: List[str] = []
    ratings: List[str] = []
    for _, row in db_df.iterrows():
        director_values = [part.strip() for part in str(row.get("international_director_detail", "")).split("|") if part.strip()]
        shareholder_values = [part.strip() for part in str(row.get("international_shareholder_detail", "")).split("|") if part.strip()]
        labels = extract_country_flags(director_values) + extract_country_flags(shareholder_values)
        if str(row.get("owner_company_name", "")).startswith("✓"):
            labels.append("🏢")
        if int(row.get("associated_company_count", 0) or 0) > 0:
            labels.append("🔗")
        signals.append(" ".join(dedupe_preserve_order(labels)))
        ratings.append(
            build_rating(
                bool(row.get("international_director", 0)),
                bool(row.get("international_shareholder", 0)),
                bool(row.get("owned_by_company", 0)),
                bool(row.get("target_sic", 0)),
                director_values,
                shareholder_values,
            )
        )

    return pd.DataFrame(
        {
            "Shortlist": db_df.get("shortlisted", pd.Series(0, index=db_df.index)).fillna(0).astype(int).astype(bool),
            "Target SIC": db_df.get("target_sic", pd.Series(0, index=db_df.index)).fillna(0).astype(int).map(lambda value: "🎯" if value else ""),
            "Rating": ratings,
            "Target Indicators": db_df.get("target_indicators", pd.Series("", index=db_df.index)).fillna(""),
            "Company Name": db_df["company_name"],
            "SIC Code": db_df["sic_code"],
            "Signals": signals,
            "International Director": db_df.get("international_director_detail", pd.Series("", index=db_df.index)).fillna(""),
            "International Shareholder": db_df.get("international_shareholder_detail", pd.Series("", index=db_df.index)).fillna(""),
            "Owned By A Company": db_df.get("owner_company_name", pd.Series("", index=db_df.index)).fillna(""),
            "Associated Companies": db_df.get("associated_companies", pd.Series("", index=db_df.index)).fillna(""),
            "Associated Count": db_df.get("associated_company_count", pd.Series(0, index=db_df.index)).fillna(0).astype(int),
            "Profile": db_df.get("profile_url", pd.Series("", index=db_df.index)).fillna(""),
            "Pulled At": db_df["pulled_at"],
            "company_number": db_df["company_number"],
        }
    )


def apply_filters(
    df: pd.DataFrame,
    only_flagged: bool,
    selected_signals: List[str],
    sic_search: str,
    company_name_search: str,
    shortlisted_only: bool,
    hide_mfg_wholesale: bool,
    associated_only: bool,
) -> pd.DataFrame:
    filtered = df.copy()
    if shortlisted_only:
        filtered = filtered[filtered["Shortlist"]].copy()
    if associated_only:
        filtered = filtered[filtered["Associated Count"] > 0].copy()
    if only_flagged:
        mask = pd.Series(False, index=filtered.index)
        if "International Director" in selected_signals:
            mask |= filtered["International Director"].astype(str).str.startswith("✓", na=False)
        if "International Shareholder" in selected_signals:
            mask |= filtered["International Shareholder"].astype(str).str.startswith("✓", na=False)
        if "Owned By A Company" in selected_signals:
            mask |= filtered["Owned By A Company"].astype(str).str.startswith("✓", na=False)
        if "Associated Companies" in selected_signals:
            mask |= filtered["Associated Count"].fillna(0).astype(int).gt(0)
        filtered = filtered[mask].copy()
    if sic_search.strip():
        filtered = filtered[filtered["SIC Code"].astype(str).str.contains(re.escape(sic_search.strip()), case=False, na=False)].copy()
    if company_name_search.strip():
        filtered = filtered[filtered["Company Name"].astype(str).str.contains(re.escape(company_name_search.strip()), case=False, na=False)].copy()
    if hide_mfg_wholesale:
        filtered = filtered[
            ~filtered["SIC Code"].astype(str).apply(
                lambda value: any(code.strip() in MANUFACTURING_WHOLESALE_SIC_CODES for code in str(value).split(","))
            )
        ].copy()
    return filtered


def render_kpis(display_df: pd.DataFrame) -> None:
    total = len(display_df)
    flagged = int(
        (
            display_df["International Director"].astype(str).str.startswith("✓", na=False)
            | display_df["International Shareholder"].astype(str).str.startswith("✓", na=False)
            | display_df["Owned By A Company"].astype(str).str.startswith("✓", na=False)
        ).sum()
    ) if total else 0
    associated = int((display_df["Associated Count"] > 0).sum()) if total else 0
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Total Results", f"{total:,}")
    c2.metric("Flagged Rows", f"{flagged:,}")
    c3.metric("Intl Directors", f"{int(display_df['International Director'].astype(str).str.startswith('✓', na=False).sum()) if total else 0:,}")
    c4.metric("Intl Shareholders", f"{int(display_df['International Shareholder'].astype(str).str.startswith('✓', na=False).sum()) if total else 0:,}")
    c5.metric("Associated Companies", f"{associated:,}")
    c6.metric("Shortlisted", f"{int(display_df['Shortlist'].sum()) if total else 0:,}")


def render_sidebar(default_date: date) -> Tuple[date, bool, bool, List[str], str, str, bool, bool, bool]:
    with st.sidebar:
        st.header("Screening controls")
        target_date = st.date_input("Incorporation date", value=default_date, format="YYYY-MM-DD")
        run = st.button("Pull new companies", type="primary", use_container_width=True)
        rebuild_associations = st.button("Rebuild associated companies", use_container_width=True)
        st.divider()
        st.subheader("Result filters")
        only_flagged = st.checkbox("Show only flagged rows", value=False)
        associated_only = st.checkbox("Show associated companies only", value=False)
        selected_signals = st.multiselect("Signals", options=SIGNAL_OPTIONS, default=SIGNAL_OPTIONS)
        hide_mfg_wholesale = st.checkbox("Hide Manufacturing & Wholesale SICs", value=False)
        sic_search = st.text_input("Filter by SIC code", placeholder="e.g. 62012")
        company_name_search = st.text_input("Filter by company name", placeholder="e.g. Labs")
        shortlisted_only = st.checkbox("Show shortlisted only", value=False)
    return (
        target_date, run, rebuild_associations, selected_signals, sic_search, company_name_search,
        only_flagged, shortlisted_only, hide_mfg_wholesale, associated_only,
    )


def main() -> None:
    apply_custom_css()
    st.title("Companies House New Incorporations Screener")
    st.caption("Screen newly incorporated companies and identify associations using stable Significant Owner keys.")
    st.markdown(
        """
        <div class="app-note">
        Associated Companies are calculated only where two screened companies share the same current Companies House Significant Owner key. Owner names are shown for context only and are never used for matching.
        </div>
        """,
        unsafe_allow_html=True,
    )

    try:
        api_keys = validate_api_keys()
    except Exception as exc:
        st.error(str(exc))
        st.stop()

    conn = init_db()
    client = CHClient(api_keys)
    (
        target_date, run, rebuild_associations, selected_signals, sic_search, company_name_search,
        only_flagged, shortlisted_only, hide_mfg_wholesale, associated_only,
    ) = render_sidebar(date.today())
    date_str = target_date.strftime("%Y-%m-%d")

    if run:
        failures: List[str] = []
        with st.status("Running Companies House screening...", expanded=True) as status:
            companies, diagnostics = search_new_companies(client, date_str)
            already_seen = existing_company_numbers(conn, date_str)
            new_companies = [company for company in companies if company.get("company_number") not in already_seen]
            st.write(f"Raw search results: {diagnostics['raw_results']}")
            st.write(f"Filtered results retained: {diagnostics['filtered_results']}")
            st.write(f"New companies to enrich: {len(new_companies)}")
            progress = st.progress(0)
            total = max(len(new_companies), 1)
            for index, item in enumerate(new_companies, start=1):
                company_number = str(item.get("company_number") or "unknown")
                try:
                    upsert_company(conn, process_company(client, conn, item, date_str))
                except Exception as exc:
                    failures.append(f"{company_number}: {exc}")
                progress.progress(min(index / total, 1.0))
            refresh_association_fields(conn)
            if failures:
                st.warning(f"Completed with {len(failures)} enrichment failures.")
                st.code("\n".join(failures[:50]))
                status.update(label="Completed with some errors", state="error")
            else:
                status.update(label="Refresh complete", state="complete")

    if rebuild_associations:
        refresh_association_fields(conn)
        st.success("Associated company relationships rebuilt from stored Significant Owner keys.")

    db_df = read_db_rows(conn, date_str)
    display_df = build_display_df(db_df)
    render_kpis(display_df)
    filtered_df = apply_filters(
        display_df, only_flagged, selected_signals, sic_search, company_name_search,
        shortlisted_only, hide_mfg_wholesale, associated_only,
    )

    tab_results, tab_shortlist, tab_settings = st.tabs(["Results", "Shortlist", "Settings"])
    visible_columns = [
        "Shortlist", "Target SIC", "Rating", "Target Indicators", "Company Name", "SIC Code", "Signals",
        "International Director", "International Shareholder", "Owned By A Company", "Associated Companies",
        "Associated Count", "Profile", "Pulled At", "company_number",
    ]

    with tab_results:
        st.subheader("Results")
        st.caption(f"{len(filtered_df):,} rows visible for {date_str}. Associations are limited to companies already stored in this app database.")
        editor_df = filtered_df[visible_columns].copy()
        edited_df = st.data_editor(
            editor_df,
            use_container_width=True,
            hide_index=True,
            disabled=[column for column in visible_columns if column != "Shortlist"],
            column_config={
                "Shortlist": st.column_config.CheckboxColumn("Shortlist", help="Tick to mark this company for follow-up."),
                "Company Name": st.column_config.TextColumn("Company Name", width="large"),
                "Associated Companies": st.column_config.TextColumn(
                    "Associated Companies",
                    width="large",
                    help="Other stored companies sharing at least one current Significant Owner key.",
                ),
                "Associated Count": st.column_config.NumberColumn("Associated Count", width="small"),
                "Profile": st.column_config.LinkColumn("Profile", display_text="Open record", width="small"),
                "company_number": None,
            },
            key=f"results_editor_{date_str}",
        )
        if not edited_df.empty:
            changes = edited_df[["company_number", "Shortlist"]].merge(
                display_df[["company_number", "Shortlist"]],
                on="company_number",
                suffixes=("_new", "_old"),
                how="left",
            )
            changed_rows = changes[changes["Shortlist_new"] != changes["Shortlist_old"]]
            for _, row in changed_rows.iterrows():
                set_shortlisted_state(conn, row["company_number"], bool(row["Shortlist_new"]))
            if not changed_rows.empty:
                st.rerun()
        csv = filtered_df.drop(columns=["company_number"], errors="ignore").to_csv(index=False).encode("utf-8")
        st.download_button(
            "Download filtered CSV",
            data=csv,
            file_name=f"companies_house_screening_{date_str}.csv",
            mime="text/csv",
            use_container_width=True,
        )

    with tab_shortlist:
        st.subheader("Shortlist")
        shortlist_df = display_df[display_df["Shortlist"]].copy()
        if shortlist_df.empty:
            st.info("No shortlisted companies yet.")
        else:
            st.dataframe(
                shortlist_df.drop(columns=["company_number"], errors="ignore"),
                use_container_width=True,
                hide_index=True,
                column_config={"Profile": st.column_config.LinkColumn("Profile", display_text="Open record")},
            )

    with tab_settings:
        st.subheader("Association method")
        st.markdown(
            """
- Match key: `significant_owner_key`, not the owner name.
- Corporate PSCs: matched by registered company number where supplied by Companies House.
- Other PSCs: matched by the Companies House PSC resource identifier from the API `links.self` URL.
- Missing stable identifiers: retained in the database but deliberately excluded from cross-company matching.
- Current ownership only: PSC records with `ceased_on` are excluded from associated-company results.
- Scope: the table compares companies already ingested into this app database; it does not claim to search the entire Companies House register.
            """
        )


if __name__ == "__main__":
    main()
