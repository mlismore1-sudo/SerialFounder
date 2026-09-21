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

st.set_page_config(page_title="Companies House Shared Shareholder Screener", layout="wide")

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
ALL_ALLOWED_SIC_CODES = sorted({*ALLOWED_SIC_CODES, *MANUFACTURING_WHOLESALE_SIC_CODES})

ALLOWED_COMPANY_TYPES = [
    "ltd", "llp", "private-limited-guarant-nsc", "private-limited-shares-section-30-exemption",
]
BONUS_STAR_COUNTRIES = {"sweden", "norway", "united states"}
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


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip().lower().replace("-", " ")
    text = re.sub(r"[^a-z0-9\s]", "", text)
    return re.sub(r"\s+", " ", text).strip()


NORMALIZED_COUNTRY_TERMS = {normalize_text(value) for value in COUNTRY_TERMS}
NORMALIZED_ALLOWED_COMPANY_TYPES = {normalize_text(value) for value in ALLOWED_COMPANY_TYPES}


def canonical_country(value: Any) -> str:
    norm = normalize_text(value)
    aliases = {
        "usa": "united states", "united states of america": "united states",
        "the netherlands": "netherlands", "hongkong": "hong kong",
    }
    norm = aliases.get(norm, norm)
    if norm in NORMALIZED_COUNTRY_TERMS:
        return norm
    return NATIONALITY_TO_COUNTRY.get(norm, "")


def dedupe(values: List[str]) -> List[str]:
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


def flagged_countries(values: List[str]) -> str:
    countries = dedupe([canonical_country(value) for value in values])
    return " | ".join(f"✓ {COUNTRY_FLAG_MAP.get(value, '🌍')} {country_label(value)}" for value in countries if value)


def country_flags(values: List[str]) -> List[str]:
    countries = dedupe([canonical_country(value) for value in values])
    return [COUNTRY_FLAG_MAP[value] for value in countries if value in COUNTRY_FLAG_MAP]


def profile_url(number: str, name: str) -> str:
    return f"https://find-and-update.company-information.service.gov.uk/company/{number}#{quote(name or 'company')}"


class CompaniesHouseClient:
    def __init__(self, api_keys: List[str]):
        self.api_keys = [str(key).strip() for key in api_keys if str(key).strip()]
        if not self.api_keys:
            raise ValueError("No Companies House API keys supplied.")
        self.index = 0
        self.session = requests.Session()

    def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        last_error = "unknown error"
        for _ in range(max(len(self.api_keys) * 3, 3)):
            try:
                response = self.session.get(
                    BASE_URL + path,
                    params=params,
                    auth=(self.api_keys[self.index], ""),
                    headers={"Accept": "application/json"},
                    timeout=30,
                )
                if response.status_code == 404:
                    return {}
                if response.status_code in (401, 403, 429):
                    last_error = f"HTTP {response.status_code}"
                    self.index = (self.index + 1) % len(self.api_keys)
                    time.sleep(0.5)
                    continue
                response.raise_for_status()
                return response.json()
            except requests.RequestException as exc:
                last_error = str(exc)
                self.index = (self.index + 1) % len(self.api_keys)
                time.sleep(0.5)
        raise RuntimeError(f"Companies House API request failed after retries: {last_error}")


def paged_items(client: CompaniesHouseClient, path: str, page_size: int, extra: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    start = 0
    while True:
        params: Dict[str, Any] = {"start_index": start}
        if extra:
            params.update(extra)
        params["size" if path == "/advanced-search/companies" else "items_per_page"] = page_size
        payload = client.get(path, params)
        batch = payload.get("items", []) or []
        items.extend(batch)
        total = int(payload.get("total_results") or payload.get("total_count") or len(items))
        start += page_size
        if not batch or start >= total:
            break
    return items


def get_officers(client: CompaniesHouseClient, number: str) -> List[Dict[str, Any]]:
    return paged_items(client, f"/company/{number}/officers", OFFICERS_PAGE_SIZE)


def get_pscs(client: CompaniesHouseClient, number: str) -> List[Dict[str, Any]]:
    return paged_items(client, f"/company/{number}/persons-with-significant-control", PSC_PAGE_SIZE)


def psc_id(psc: Dict[str, Any]) -> str:
    link = str((psc.get("links") or {}).get("self") or "").rstrip("/")
    return link.split("/")[-1] if link else ""


def corporate_number(psc: Dict[str, Any]) -> str:
    identification = psc.get("identification") or {}
    for value in [psc.get("company_number"), identification.get("registration_number"), identification.get("company_number")]:
        if value:
            return str(value).strip().upper()
    return ""


def significant_owner_key(psc: Dict[str, Any]) -> str:
    corporate = corporate_number(psc)
    if corporate:
        return f"corporate:{corporate}"
    identifier = psc_id(psc)
    if identifier:
        return f"psc:{str(psc.get('kind') or 'unknown').lower()}:{identifier}"
    return f"unmatched:{str(psc.get('kind') or 'unknown').lower()}:{json.dumps(psc, sort_keys=True, default=str)}"


def is_individual_psc(kind: str) -> bool:
    value = str(kind or "").lower()
    return "individual" in value and "corporate" not in value


def ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        conn.commit()


def init_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS screened_companies (
            company_number TEXT PRIMARY KEY,
            company_name TEXT,
            sic_code TEXT,
            incorporation_date TEXT,
            company_type TEXT,
            international_director INTEGER DEFAULT 0,
            international_shareholder INTEGER DEFAULT 0,
            owned_by_company INTEGER DEFAULT 0,
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
            PRIMARY KEY(company_number, significant_owner_key)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_psc_owner_key ON psc_records(significant_owner_key)")
    conn.commit()
    migrations = [
        ("international_director_detail", "TEXT"),
        ("international_shareholder_detail", "TEXT"),
        ("owner_company_name", "TEXT"),
        ("profile_url", "TEXT"),
        ("shortlisted", "INTEGER DEFAULT 0"),
        ("target_sic", "INTEGER DEFAULT 0"),
        ("target_address", "INTEGER DEFAULT 0"),
        ("target_address_detail", "TEXT"),
        ("target_indicators", "TEXT"),
        ("associated_company_count", "INTEGER DEFAULT 0"),
        ("associated_companies", "TEXT"),
        ("personal_associated_count", "INTEGER DEFAULT 0"),
        ("personal_associated_companies", "TEXT"),
        ("personal_associated_people", "TEXT"),
    ]
    for column, definition in migrations:
        ensure_column(conn, "screened_companies", column, definition)
    return conn


def validate_api_keys() -> List[str]:
    if "COMPANIES_HOUSE_API_KEYS" not in st.secrets:
        raise ValueError("Missing COMPANIES_HOUSE_API_KEYS in .streamlit/secrets.toml")
    keys = [str(key).strip() for key in st.secrets["COMPANIES_HOUSE_API_KEYS"] if str(key).strip()]
    if not keys:
        raise ValueError("COMPANIES_HOUSE_API_KEYS is empty")
    return keys


def search_companies(client: CompaniesHouseClient, target_date: str) -> List[Dict[str, Any]]:
    params = {
        "incorporated_from": target_date,
        "incorporated_to": target_date,
        "company_status": "active",
        "company_type": ",".join(ALLOWED_COMPANY_TYPES),
        "sic_codes": ",".join(ALL_ALLOWED_SIC_CODES),
    }
    raw_items = paged_items(client, "/advanced-search/companies", SEARCH_PAGE_SIZE, params)
    filtered = [
        item for item in raw_items
        if item.get("company_status", "").lower() == "active"
        and normalize_text(item.get("company_type")) in NORMALIZED_ALLOWED_COMPANY_TYPES
        and any(str(code) in ALL_ALLOWED_SIC_CODES for code in (item.get("sic_codes") or []))
    ]
    return list({item["company_number"]: item for item in filtered if item.get("company_number")}.values())


def store_pscs(conn: sqlite3.Connection, number: str, pscs: List[Dict[str, Any]]) -> None:
    conn.execute("DELETE FROM psc_records WHERE company_number=?", (number,))
    for psc in pscs:
        conn.execute(
            """
            INSERT OR REPLACE INTO psc_records (
                company_number, significant_owner_key, psc_id, psc_kind, psc_name,
                corporate_company_number, nationality, country_of_residence, notified_on,
                ceased_on, natures_of_control, source_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                number, significant_owner_key(psc), psc_id(psc), str(psc.get("kind") or ""), str(psc.get("name") or ""),
                corporate_number(psc), str(psc.get("nationality") or ""), str(psc.get("country_of_residence") or ""),
                str(psc.get("notified_on") or ""), str(psc.get("ceased_on") or ""),
                json.dumps(psc.get("natures_of_control") or []), json.dumps(psc),
            ),
        )
    conn.commit()


def officer_details(client: CompaniesHouseClient, number: str) -> Tuple[bool, List[str], int]:
    countries: List[str] = []
    count = 0
    for officer in get_officers(client, number):
        role = normalize_text(officer.get("officer_role"))
        if "director" not in role and role != "designated member":
            continue
        count += 1
        for value in [officer.get("country_of_residence"), (officer.get("address") or {}).get("country"), officer.get("nationality")]:
            if canonical_country(value):
                countries.append(str(value))
    countries = dedupe(countries)
    return bool(countries), countries, count


def has_bonus_star(values: List[str]) -> bool:
    return bool({canonical_country(value) for value in values} & BONUS_STAR_COUNTRIES)


def process_company(client: CompaniesHouseClient, conn: sqlite3.Connection, item: Dict[str, Any], target_date: str) -> Dict[str, Any]:
    number = str(item.get("company_number") or "")
    name = str(item.get("company_name") or item.get("title") or "")
    international_director, director_countries, director_count = officer_details(client, number)
    pscs = get_pscs(client, number)
    store_pscs(conn, number, pscs)

    shareholder_countries: List[str] = []
    owner_names: List[str] = []
    international_shareholder = False
    owned_by_company = False
    for psc in pscs:
        if psc.get("ceased_on"):
            continue
        kind = str(psc.get("kind") or "")
        values = [psc.get("country_of_residence"), (psc.get("address") or {}).get("country"), psc.get("nationality")]
        matches = [str(value) for value in values if canonical_country(value)]
        shareholder_countries.extend(matches)
        international_shareholder = international_shareholder or bool(matches)
        if kind in COMPANY_OWNER_KINDS or "corporate" in kind or "legal-person" in kind:
            owned_by_company = True
            if str(psc.get("name") or "").strip():
                owner_names.append(str(psc["name"]).strip())

    shareholder_countries = dedupe(shareholder_countries)
    owner_names = dedupe(owner_names)
    target_sic = any(str(code) in TARGET_SIC_CODES for code in (item.get("sic_codes") or []))
    address = item.get("registered_office_address") or item.get("address") or {}
    address_country = canonical_country(address.get("country"))
    target_address = bool(address_country)

    indicators: List[str] = []
    if target_sic:
        indicators.append("🎯")
    if target_address:
        indicators.append("🏳️")
    indicators.extend(dedupe(country_flags(director_countries) + country_flags(shareholder_countries)))
    if director_count >= 2:
        indicators.append(f"{director_count} directors")

    return {
        "company_number": number,
        "company_name": name,
        "sic_code": ", ".join(str(code) for code in (item.get("sic_codes") or []) if str(code) in ALL_ALLOWED_SIC_CODES),
        "incorporation_date": target_date,
        "company_type": item.get("company_type", ""),
        "international_director": international_director,
        "international_director_detail": flagged_countries(director_countries),
        "international_shareholder": international_shareholder,
        "international_shareholder_detail": flagged_countries(shareholder_countries),
        "owned_by_company": owned_by_company,
        "owner_company_name": " | ".join(owner_names),
        "pulled_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        "raw_json": item,
        "profile_url": profile_url(number, name),
        "shortlisted": False,
        "target_sic": target_sic,
        "target_address": target_address,
        "target_address_detail": f"✓ {COUNTRY_FLAG_MAP.get(address_country, '🌍')} {country_label(address_country)}" if address_country else "",
        "target_indicators": " ".join(indicators),
        "associated_company_count": 0,
        "associated_companies": "",
        "personal_associated_count": 0,
        "personal_associated_companies": "",
        "personal_associated_people": "",
    }


def rebuild_associations(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        """
        SELECT p.company_number, p.significant_owner_key, p.psc_name, p.psc_kind,
               COALESCE(c.company_name, p.company_number)
        FROM psc_records p
        LEFT JOIN screened_companies c ON c.company_number=p.company_number
        WHERE COALESCE(p.ceased_on, '')=''
          AND p.significant_owner_key NOT LIKE 'unmatched:%'
        """
    ).fetchall()

    all_groups: Dict[str, Dict[str, str]] = {}
    personal_groups: Dict[str, Dict[str, Dict[str, str]]] = {}
    for number, owner_key, owner_name, kind, company_name in rows:
        all_groups.setdefault(owner_key, {})[number] = company_name or number
        if is_individual_psc(kind):
            personal_groups.setdefault(owner_key, {})[number] = {
                "company_name": company_name or number,
                "owner_name": owner_name or owner_key,
            }

    all_associations: Dict[str, Dict[str, str]] = {}
    for members in all_groups.values():
        if len(members) > 1:
            for number in members:
                all_associations.setdefault(number, {}).update({other: name for other, name in members.items() if other != number})

    personal_associations: Dict[str, Dict[str, Dict[str, str]]] = {}
    for members in personal_groups.values():
        if len(members) > 1:
            for number, current in members.items():
                for other, other_info in members.items():
                    if other != number:
                        personal_associations.setdefault(number, {})[current["owner_name"]] = {
                            "company_number": other,
                            "company_name": other_info["company_name"],
                        }

    company_numbers = [row[0] for row in conn.execute("SELECT company_number FROM screened_companies").fetchall()]
    for number in company_numbers:
        all_related = all_associations.get(number, {})
        personal_related = personal_associations.get(number, {})
        all_text = " | ".join(f"{name} ({other})" for other, name in sorted(all_related.items(), key=lambda item: item[1].lower()))
        personal_company_values = sorted(
            {f"{value['company_name']} ({value['company_number']})" for value in personal_related.values()},
            key=str.lower,
        )
        personal_people_text = " | ".join(sorted(personal_related, key=str.lower))
        conn.execute(
            """
            UPDATE screened_companies
            SET associated_company_count=?, associated_companies=?,
                personal_associated_count=?, personal_associated_companies=?, personal_associated_people=?
            WHERE company_number=?
            """,
            (
                len(all_related), all_text, len(personal_company_values),
                " | ".join(personal_company_values), personal_people_text, number,
            ),
        )
    conn.commit()


def upsert_company(conn: sqlite3.Connection, row: Dict[str, Any]) -> None:
    columns = [
        "company_number", "company_name", "sic_code", "incorporation_date", "company_type",
        "international_director", "international_director_detail", "international_shareholder",
        "international_shareholder_detail", "owned_by_company", "owner_company_name", "pulled_at", "raw_json",
        "profile_url", "shortlisted", "target_sic", "target_address", "target_address_detail", "target_indicators",
        "associated_company_count", "associated_companies", "personal_associated_count",
        "personal_associated_companies", "personal_associated_people",
    ]
    values = [json.dumps(row.get(column, {})) if column == "raw_json" else row.get(column) for column in columns]
    placeholders = ",".join("?" for _ in columns)
    updates = ",".join(f"{column}=excluded.{column}" for column in columns[1:] if column != "shortlisted")
    conn.execute(
        f"INSERT INTO screened_companies ({','.join(columns)}) VALUES ({placeholders}) "
        f"ON CONFLICT(company_number) DO UPDATE SET {updates}",
        values,
    )
    conn.commit()


def read_rows(conn: sqlite3.Connection, incorporation_date: str) -> pd.DataFrame:
    return pd.read_sql_query(
        "SELECT * FROM screened_companies WHERE incorporation_date=? ORDER BY pulled_at DESC",
        conn,
        params=(incorporation_date,),
    )


def build_display(df: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "Shortlist", "Target SIC", "Rating", "Target Indicators", "Company Name", "SIC Code", "Signals",
        "International Director", "International Shareholder", "Owned By A Company", "Associated Companies",
        "Associated Count", "Personal Shareholders Also In Other Businesses", "Personal Shareholder Other Companies",
        "Personal Shareholder Company Count", "Profile", "Pulled At", "company_number",
    ]
    if df.empty:
        return pd.DataFrame(columns=columns)

    signals: List[str] = []
    ratings: List[str] = []
    for _, row in df.iterrows():
        director_values = str(row.get("international_director_detail", "")).split("|")
        shareholder_values = str(row.get("international_shareholder_detail", "")).split("|")
        labels = country_flags(director_values) + country_flags(shareholder_values)
        if str(row.get("owner_company_name", "")).strip():
            labels.append("🏢")
        if int(row.get("associated_company_count", 0) or 0) > 0:
            labels.append("🔗")
        if int(row.get("personal_associated_count", 0) or 0) > 0:
            labels.append("👤")
        signals.append(" ".join(dedupe(labels)))
        stars = sum(bool(row.get(key, 0)) for key in ["international_director", "international_shareholder", "owned_by_company", "target_sic"])
        if has_bonus_star(director_values + shareholder_values):
            stars += 1
        ratings.append("⭐" * stars)

    return pd.DataFrame({
        "Shortlist": df["shortlisted"].fillna(0).astype(bool),
        "Target SIC": df["target_sic"].fillna(0).map(lambda value: "🎯" if value else ""),
        "Rating": ratings,
        "Target Indicators": df["target_indicators"].fillna(""),
        "Company Name": df["company_name"],
        "SIC Code": df["sic_code"],
        "Signals": signals,
        "International Director": df["international_director_detail"].fillna(""),
        "International Shareholder": df["international_shareholder_detail"].fillna(""),
        "Owned By A Company": df["owner_company_name"].fillna(""),
        "Associated Companies": df["associated_companies"].fillna(""),
        "Associated Count": df["associated_company_count"].fillna(0).astype(int),
        "Personal Shareholders Also In Other Businesses": df["personal_associated_people"].fillna(""),
        "Personal Shareholder Other Companies": df["personal_associated_companies"].fillna(""),
        "Personal Shareholder Company Count": df["personal_associated_count"].fillna(0).astype(int),
        "Profile": df["profile_url"].fillna(""),
        "Pulled At": df["pulled_at"],
        "company_number": df["company_number"],
    })


def main() -> None:
    st.title("Companies House Shared Shareholder Screener")
    st.caption("Find current individual significant owners who are also current individual significant owners of other stored companies.")

    try:
        api_keys = validate_api_keys()
    except Exception as exc:
        st.error(str(exc))
        st.stop()

    conn = init_db()
    client = CompaniesHouseClient(api_keys)

    with st.sidebar:
        target_date = st.date_input("Incorporation date", value=date.today(), format="YYYY-MM-DD")
        run = st.button("Pull new companies", type="primary", use_container_width=True)
        rebuild = st.button("Rebuild shareholder associations", use_container_width=True)
        personal_only = st.checkbox("Show shared personal shareholders only")
        associated_only = st.checkbox("Show associated companies only")
        shortlisted_only = st.checkbox("Show shortlisted only")
        hide_mfg = st.checkbox("Hide Manufacturing & Wholesale SICs")
        name_search = st.text_input("Filter by company name")
        sic_search = st.text_input("Filter by SIC code")

    date_str = target_date.strftime("%Y-%m-%d")

    if run:
        with st.status("Running Companies House screening...", expanded=True) as status:
            companies = search_companies(client, date_str)
            existing = {row[0] for row in conn.execute("SELECT company_number FROM screened_companies WHERE incorporation_date=?", (date_str,)).fetchall()}
            new_companies = [company for company in companies if company.get("company_number") not in existing]
            st.write(f"Companies found: {len(companies):,}; new enrichments: {len(new_companies):,}")
            progress = st.progress(0)
            failures: List[str] = []
            for index, item in enumerate(new_companies, start=1):
                try:
                    upsert_company(conn, process_company(client, conn, item, date_str))
                except Exception as exc:
                    failures.append(f"{item.get('company_number')}: {exc}")
                progress.progress(index / max(len(new_companies), 1))
            rebuild_associations(conn)
            if failures:
                st.warning("Some records failed to enrich.")
                st.code("\n".join(failures[:50]))
                status.update(label="Completed with errors", state="error")
            else:
                status.update(label="Refresh complete", state="complete")

    if rebuild:
        rebuild_associations(conn)
        st.success("Shareholder associations rebuilt.")

    display = build_display(read_rows(conn, date_str))
    if personal_only:
        display = display[display["Personal Shareholder Company Count"] > 0]
    if associated_only:
        display = display[display["Associated Count"] > 0]
    if shortlisted_only:
        display = display[display["Shortlist"]]
    if name_search.strip():
        display = display[display["Company Name"].astype(str).str.contains(re.escape(name_search.strip()), case=False, na=False)]
    if sic_search.strip():
        display = display[display["SIC Code"].astype(str).str.contains(re.escape(sic_search.strip()), case=False, na=False)]
    if hide_mfg:
        display = display[
            ~display["SIC Code"].astype(str).apply(
                lambda value: any(code.strip() in MANUFACTURING_WHOLESALE_SIC_CODES for code in value.split(","))
            )
        ]

    st.metric("Visible results", f"{len(display):,}")
    edited = st.data_editor(
        display,
        use_container_width=True,
        hide_index=True,
        disabled=[column for column in display.columns if column != "Shortlist"],
        column_config={
            "Shortlist": st.column_config.CheckboxColumn("Shortlist"),
            "Associated Companies": st.column_config.TextColumn("Associated Companies", width="large"),
            "Personal Shareholders Also In Other Businesses": st.column_config.TextColumn("Personal Shareholders Also In Other Businesses", width="medium"),
            "Personal Shareholder Other Companies": st.column_config.TextColumn("Personal Shareholder Other Companies", width="large"),
            "Personal Shareholder Company Count": st.column_config.NumberColumn("Personal Shareholder Company Count", width="small"),
            "Profile": st.column_config.LinkColumn("Profile", display_text="Open record"),
            "company_number": None,
        },
        key=f"results_{date_str}",
    )

    if not edited.empty:
        for _, row in edited[["company_number", "Shortlist"]].iterrows():
            conn.execute(
                "UPDATE screened_companies SET shortlisted=? WHERE company_number=?",
                (int(bool(row["Shortlist"])), row["company_number"]),
            )
        conn.commit()

    st.download_button(
        "Download filtered CSV",
        data=display.drop(columns=["company_number"], errors="ignore").to_csv(index=False).encode("utf-8"),
        file_name=f"companies_house_shared_shareholders_{date_str}.csv",
        mime="text/csv",
        use_container_width=True,
    )


if __name__ == "__main__":
    main()
    "46520", "46530", "46610", "46620", "46630", "46640", "46650", "46660", "46690", "46711", "46719",
    "46720", "46730", "46740", "46750", "46900",
}
ALL_ALLOWED_SIC_CODES = list({*ALLOWED_SIC_CODES, *MANUFACTURING_WHOLESALE_SIC_CODES})

ALLOWED_COMPANY_TYPES = [
    "ltd", "llp", "private-limited-guarant-nsc", "private-limited-shares-section-30-exemption",
]
BONUS_STAR_COUNTRIES = {"sweden", "norway", "united states"}
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


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip().lower().replace("-", " ")
    text = re.sub(r"[^a-z0-9\s]", "", text)
    return re.sub(r"\s+", " ", text).strip()


NORMALIZED_COUNTRY_TERMS = {normalize_text(value) for value in COUNTRY_TERMS}
NORMALIZED_ALLOWED_COMPANY_TYPES = {normalize_text(value) for value in ALLOWED_COMPANY_TYPES}


def canonical_country(value: Any) -> str:
    norm = normalize_text(value)
    aliases = {
        "usa": "united states", "united states of america": "united states",
        "the netherlands": "netherlands", "hongkong": "hong kong",
    }
    norm = aliases.get(norm, norm)
    if norm in NORMALIZED_COUNTRY_TERMS:
        return norm
    return NATIONALITY_TO_COUNTRY.get(norm, "")


def dedupe(values: List[str]) -> List[str]:
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


def flagged_countries(values: List[str]) -> str:
    countries = dedupe([canonical_country(value) for value in values])
    return " | ".join(f"✓ {COUNTRY_FLAG_MAP.get(value, '🌍')} {country_label(value)}" for value in countries if value)


def flags(values: List[str]) -> List[str]:
    countries = dedupe([canonical_country(value) for value in values])
    return [COUNTRY_FLAG_MAP[value] for value in countries if value in COUNTRY_FLAG_MAP]


def profile_url(number: str, name: str) -> str:
    return f"https://find-and-update.company-information.service.gov.uk/company/{number}#{quote(name or 'company')}"


class CompaniesHouseClient:
    def __init__(self, api_keys: List[str]):
        self.api_keys = [str(key).strip() for key in api_keys if str(key).strip()]
        if not self.api_keys:
            raise ValueError("No Companies House API keys supplied.")
        self.index = 0
        self.session = requests.Session()

    def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        last_error = "unknown error"
        for _ in range(max(len(self.api_keys) * 3, 3)):
            try:
                response = self.session.get(
                    BASE_URL + path,
                    params=params,
                    auth=(self.api_keys[self.index], ""),
                    headers={"Accept": "application/json"},
                    timeout=30,
                )
                if response.status_code == 404:
                    return {}
                if response.status_code in (401, 403, 429):
                    last_error = f"HTTP {response.status_code}"
                    self.index = (self.index + 1) % len(self.api_keys)
                    time.sleep(0.5)
                    continue
                response.raise_for_status()
                return response.json()
            except requests.RequestException as exc:
                last_error = str(exc)
                self.index = (self.index + 1) % len(self.api_keys)
                time.sleep(0.5)
        raise RuntimeError(f"Companies House API request failed after retries: {last_error}")


def paged_items(client: CompaniesHouseClient, path: str, page_size: int, extra: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    start = 0
    while True:
        params: Dict[str, Any] = {"start_index": start}
        if extra:
            params.update(extra)
        params["size" if path == "/advanced-search/companies" else "items_per_page"] = page_size
        payload = client.get(path, params)
        batch = payload.get("items", []) or []
        items.extend(batch)
        total = int(payload.get("total_results") or payload.get("total_count") or len(items))
        start += page_size
        if not batch or start >= total:
            break
    return items


def get_officers(client: CompaniesHouseClient, number: str) -> List[Dict[str, Any]]:
    return paged_items(client, f"/company/{number}/officers", OFFICERS_PAGE_SIZE)


def get_pscs(client: CompaniesHouseClient, number: str) -> List[Dict[str, Any]]:
    return paged_items(client, f"/company/{number}/persons-with-significant-control", PSC_PAGE_SIZE)


def psc_id(psc: Dict[str, Any]) -> str:
    link = str((psc.get("links") or {}).get("self") or "").rstrip("/")
    return link.split("/")[-1] if link else ""


def corporate_number(psc: Dict[str, Any]) -> str:
    identification = psc.get("identification") or {}
    for value in [psc.get("company_number"), identification.get("registration_number"), identification.get("company_number")]:
        if value:
            return str(value).strip().upper()
    return ""


def significant_owner_key(psc: Dict[str, Any]) -> str:
    corporate = corporate_number(psc)
    if corporate:
        return f"corporate:{corporate}"
    identifier = psc_id(psc)
    if identifier:
        return f"psc:{str(psc.get('kind') or 'unknown').lower()}:{identifier}"
    return f"unmatched:{str(psc.get('kind') or 'unknown').lower()}:{json.dumps(psc, sort_keys=True, default=str)}"


def is_individual_psc(kind: str) -> bool:
    value = str(kind or "").lower()
    return "individual" in value and "corporate" not in value


def ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        conn.commit()


def init_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS screened_companies (
            company_number TEXT PRIMARY KEY,
            company_name TEXT,
            sic_code TEXT,
            incorporation_date TEXT,
            company_type TEXT,
            international_director INTEGER DEFAULT 0,
            international_shareholder INTEGER DEFAULT 0,
            owned_by_company INTEGER DEFAULT 0,
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
            PRIMARY KEY(company_number, significant_owner_key)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_psc_owner_key ON psc_records(significant_owner_key)")
    conn.commit()
    for column, definition in [
        ("international_director_detail", "TEXT"),
        ("international_shareholder_detail", "TEXT"),
        ("owner_company_name", "TEXT"),
        ("profile_url", "TEXT"),
        ("shortlisted", "INTEGER DEFAULT 0"),
        ("target_sic", "INTEGER DEFAULT 0"),
        ("target_address", "INTEGER DEFAULT 0"),
        ("target_address_detail", "TEXT"),
        ("target_indicators", "TEXT"),
        ("associated_company_count", "INTEGER DEFAULT 0"),
        ("associated_companies", "TEXT"),
        ("personal_associated_count", "INTEGER DEFAULT 0"),
        ("personal_associated_companies", "TEXT"),
        ("personal_associated_people", "TEXT"),
    ]:
        ensure_column(conn, "screened_companies", column, definition)
    return conn


def validate_api_keys() -> List[str]:
    if "COMPANIES_HOUSE_API_KEYS" not in st.secrets:
        raise ValueError("Missing COMPANIES_HOUSE_API_KEYS in .streamlit/secrets.toml")
    keys = [str(key).strip() for key in st.secrets["COMPANIES_HOUSE_API_KEYS"] if str(key).strip()]
    if not keys:
        raise ValueError("COMPANIES_HOUSE_API_KEYS is empty")
    return keys


def search_companies(client: CompaniesHouseClient, target_date: str) -> List[Dict[str, Any]]:
    params = {
        "incorporated_from": target_date,
        "incorporated_to": target_date,
        "company_status": "active",
        "company_type": ",".join(ALLOWED_COMPANY_TYPES),
        "sic_codes": ",".join(ALL_ALLOWED_SIC_CODES),
    }
    items = paged_items(client, "/advanced-search/companies", SEARCH_PAGE_SIZE, params)
    filtered = [
        item for item in items
        if item.get("company_status", "").lower() == "active"
        and normalize_text(item.get("company_type")) in NORMALIZED_ALLOWED_COMPANY_TYPES
        and any(str(code) in ALL_ALLOWED_SIC_CODES for code in (item.get("sic_codes") or []))
    ]
    return list({item["company_number"]: item for item in filtered if item.get("company_number")}.values())


def store_pscs(conn: sqlite3.Connection, number: str, pscs: List[Dict[str, Any]]) -> None:
    conn.execute("DELETE FROM psc_records WHERE company_number=?", (number,))
    for psc in pscs:
        conn.execute(
            """
            INSERT OR REPLACE INTO psc_records (
                company_number, significant_owner_key, psc_id, psc_kind, psc_name,
                corporate_company_number, nationality, country_of_residence, notified_on,
                ceased_on, natures_of_control, source_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                number, significant_owner_key(psc), psc_id(psc), str(psc.get("kind") or ""), str(psc.get("name") or ""),
                corporate_number(psc), str(psc.get("nationality") or ""), str(psc.get("country_of_residence") or ""),
                str(psc.get("notified_on") or ""), str(psc.get("ceased_on") or ""),
                json.dumps(psc.get("natures_of_control") or []), json.dumps(psc),
            ),
        )
    conn.commit()


def officer_details(client: CompaniesHouseClient, number: str) -> Tuple[bool, List[str], int]:
    countries: List[str] = []
    count = 0
    for officer in get_officers(client, number):
        role = normalize_text(officer.get("officer_role"))
        if "director" not in role and role != "designated member":
            continue
        count += 1
        for value in [officer.get("country_of_residence"), (officer.get("address") or {}).get("country"), officer.get("nationality")]:
            if canonical_country(value):
                countries.append(str(value))
    countries = dedupe(countries)
    return bool(countries), countries, count


def process_company(client: CompaniesHouseClient, conn: sqlite3.Connection, item: Dict[str, Any], target_date: str) -> Dict[str, Any]:
    number = str(item.get("company_number") or "")
    name = str(item.get("company_name") or item.get("title") or "")
    international_director, director_countries, director_count = officer_details(client, number)
    pscs = get_pscs(client, number)
    store_pscs(conn, number, pscs)

    shareholder_countries: List[str] = []
    owner_names: List[str] = []
    international_shareholder = False
    owned_by_company = False
    for psc in pscs:
        if psc.get("ceased_on"):
            continue
        kind = str(psc.get("kind") or "")
        values = [psc.get("country_of_residence"), (psc.get("address") or {}).get("country"), psc.get("nationality")]
        matched = [str(value) for value in values if canonical_country(value)]
        shareholder_countries.extend(matched)
        international_shareholder |= bool(matched)
        if kind in COMPANY_OWNER_KINDS or "corporate" in kind or "legal-person" in kind:
            owned_by_company = True
            if str(psc.get("name") or "").strip():
                owner_names.append(str(psc["name"]).strip())

    shareholder_countries = dedupe(shareholder_countries)
    owner_names = dedupe(owner_names)
    target_sic = any(str(code) in TARGET_SIC_CODES for code in (item.get("sic_codes") or []))
    address = item.get("registered_office_address") or item.get("address") or {}
    address_country = canonical_country(address.get("country"))
    target_address = bool(address_country)

    indicators: List[str] = []
    if target_sic:
        indicators.append("🎯")
    if target_address:
        indicators.append("🏳️")
    indicators.extend(dedupe(flags(director_countries) + flags(shareholder_countries)))
    if director_count >= 2:
        indicators.append(f"{director_count} directors")

    stars = sum(bool(value) for value in [international_director, international_shareholder, owned_by_company, target_sic])
    if has_bonus_star(director_countries + shareholder_countries):
        stars += 1

    matching_sics = [str(code) for code in (item.get("sic_codes") or []) if str(code) in ALL_ALLOWED_SIC_CODES]
    return {
        "company_number": number,
        "company_name": name,
        "sic_code": ", ".join(matching_sics),
        "incorporation_date": target_date,
        "company_type": item.get("company_type", ""),
        "international_director": international_director,
        "international_director_detail": flagged_countries(director_countries),
        "international_shareholder": international_shareholder,
        "international_shareholder_detail": flagged_countries(shareholder_countries),
        "owned_by_company": owned_by_company,
        "owner_company_name": " | ".join(owner_names),
        "pulled_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        "raw_json": item,
        "profile_url": profile_url(number, name),
        "shortlisted": False,
        "target_sic": target_sic,
        "target_address": target_address,
        "target_address_detail": f"✓ {COUNTRY_FLAG_MAP.get(address_country, '🌍')} {country_label(address_country)}" if address_country else "",
        "target_indicators": " ".join(indicators),
        "associated_company_count": 0,
        "associated_companies": "",
        "personal_associated_count": 0,
        "personal_associated_companies": "",
        "personal_associated_people": "",
    }


def has_bonus_star(values: List[str]) -> bool:
    return bool({canonical_country(value) for value in values} & BONUS_STAR_COUNTRIES)


def rebuild_all_associations(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        """
        SELECT p.company_number, p.significant_owner_key, p.psc_name, p.psc_kind,
               COALESCE(c.company_name, p.company_number)
        FROM psc_records p
        LEFT JOIN screened_companies c ON c.company_number=p.company_number
        WHERE COALESCE(p.ceased_on, '')=''
          AND p.significant_owner_key NOT LIKE 'unmatched:%'
        """
    ).fetchall()
    all_groups: Dict[str, Dict[str, str]] = {}
    personal_groups: Dict[str, Dict[str, Dict[str, str]]] = {}
    for number, owner_key, owner_name, kind, company_name in rows:
        all_groups.setdefault(owner_key, {})[number] = company_name or number
        if is_individual_psc(kind):
            personal_groups.setdefault(owner_key, {}).setdefault(number, {
                "company_name": company_name or number,
                "owner_name": owner_name or owner_key,
            })

    all_assoc: Dict[str, Dict[str, str]] = {}
    for members in all_groups.values():
        if len(members) > 1:
            for number in members:
                all_assoc.setdefault(number, {}).update({other: name for other, name in members.items() if other != number})

    personal_assoc: Dict[str, Dict[str, Dict[str, str]]] = {}
    for members in personal_groups.values():
        if len(members) > 1:
            for number, current in members.items():
                for other, other_info in members.items():
                    if other != number:
                        personal_assoc.setdefault(number, {})[current["owner_name"]] = {
                            "company_number": other,
                            "company_name": other_info["company_name"],
                        }

    company_numbers = [row[0] for row in conn.execute("SELECT company_number FROM screened_companies").fetchall()]
    for number in company_numbers:
        related_all = all_assoc.get(number, {})
        related_personal = personal_assoc.get(number, {})
        all_text = " | ".join(f"{name} ({other})" for other, name in sorted(related_all.items(), key=lambda item: item[1].lower()))
        personal_companies = sorted({item["company_name"] + " (" + item["company_number"] + ")" for item in related_personal.values()}, key=str.lower)
        people_text = " | ".join(sorted(related_personal, key=str.lower))
        conn.execute(
            """
            UPDATE screened_companies
            SET associated_company_count=?, associated_companies=?,
                personal_associated_count=?, personal_associated_companies=?, personal_associated_people=?
            WHERE company_number=?
            """,
            (len(related_all), all_text, len(personal_companies), " | ".join(personal_companies), people_text, number),
        )
    conn.commit()


def upsert_company(conn: sqlite3.Connection, row: Dict[str, Any]) -> None:
    columns = [
        "company_number", "company_name", "sic_code", "incorporation_date", "company_type",
        "international_director", "international_director_detail", "international_shareholder",
        "international_shareholder_detail", "owned_by_company", "owner_company_name", "pulled_at", "raw_json",
        "profile_url", "shortlisted", "target_sic", "target_address", "target_address_detail", "target_indicators",
        "associated_company_count", "associated_companies", "personal_associated_count",
        "personal_associated_companies", "personal_associated_people",
    ]
    values = [
        row[column] if column not in {"raw_json"} else json.dumps(row.get(column, {})) for column in columns
    ]
    placeholders = ",".join("?" for _ in columns)
    updates = ",".join(f"{column}=excluded.{column}" for column in columns[1:] if column != "shortlisted")
    conn.execute(
        f"INSERT INTO screened_companies ({','.join(columns)}) VALUES ({placeholders}) ON CONFLICT(company_number) DO UPDATE SET {updates}",
        values,
    )
    conn.commit()


def build_display(df: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "Shortlist", "Target SIC", "Rating", "Target Indicators", "Company Name", "SIC Code", "Signals",
        "International Director", "International Shareholder", "Owned By A Company", "Associated Companies",
        "Associated Count", "Personal Shareholders Also In Other Businesses", "Personal Shareholder Other Companies",
        "Personal Shareholder Company Count", "Profile", "Pulled At", "company_number",
    ]
    if df.empty:
        return pd.DataFrame(columns=columns)
    signals: List[str] = []
    ratings: List[str] = []
    for _, row in df.iterrows():
        director_values = str(row.get("international_director_detail", "")).split("|")
        shareholder_values = str(row.get("international_shareholder_detail", "")).split("|")
        labels = flags(director_values) + flags(shareholder_values)
        if str(row.get("owner_company_name", "")).strip():
            labels.append("🏢")
        if int(row.get("associated_company_count", 0) or 0) > 0:
            labels.append("🔗")
        if int(row.get("personal_associated_count", 0) or 0) > 0:
            labels.append("👤")
        signals.append(" ".join(dedupe(labels)))
        stars = sum(bool(row.get(key, 0)) for key in ["international_director", "international_shareholder", "owned_by_company", "target_sic"])
        if has_bonus_star(director_values + shareholder_values):
            stars += 1
        ratings.append("⭐" * stars)
    return pd.DataFrame({
        "Shortlist": df["shortlisted"].fillna(0).astype(bool),
        "Target SIC": df["target_sic"].fillna(0).map(lambda value: "🎯" if value else ""),
        "Rating": ratings,
        "Target Indicators": df["target_indicators"].fillna(""),
        "Company Name": df["company_name"], "SIC Code": df["sic_code"], "Signals": signals,
        "International Director": df["international_director_detail"].fillna(""),
        "International Shareholder": df["international_shareholder_detail"].fillna(""),
        "Owned By A Company": df["owner_company_name"].fillna(""),
        "Associated Companies": df["associated_companies"].fillna(""),
        "Associated Count": df["associated_company_count"].fillna(0).astype(int),
        "Personal Shareholders Also In Other Businesses": df["personal_associated_people"].fillna(""),
        "Personal Shareholder Other Companies": df["personal_associated_companies"].fillna(""),
        "Personal Shareholder Company Count": df["personal_associated_count"].fillna(0).astype(int),
        "Profile": df["profile_url"].fillna(""), "Pulled At": df["pulled_at"], "company_number": df["company_number"],
    })


def main() -> None:
    st.title("Companies House Personal Shareholder Screener")
    st.caption("Find current individual significant owners who are also current individual significant owners of other stored companies.")
    try:
        api_keys = validate_api_keys()
    except Exception as exc:
        st.error(str(exc))
        st.stop()
    conn = init_db()
    client = CompaniesHouseClient(api_keys)
    with st.sidebar:
        target_date = st.date_input("Incorporation date", value=date.today(), format="YYYY-MM-DD")
        run = st.button("Pull new companies", type="primary", use_container_width=True)
        rebuild = st.button("Rebuild shareholder associations", use_container_width=True)
        personal_only = st.checkbox("Show shared personal shareholders only")
        associated_only = st.checkbox("Show associated companies only")
        shortlisted_only = st.checkbox("Show shortlisted only")
        hide_mfg = st.checkbox("Hide Manufacturing & Wholesale SICs")
        name_search = st.text_input("Filter by company name")
        sic_search = st.text_input("Filter by SIC code")
    date_str = target_date.strftime("%Y-%m-%d")

    if run:
        with st.status("Running Companies House screening...", expanded=True) as status:
            companies = search_companies(client, date_str)
            existing = {row[0] for row in conn.execute("SELECT company_number FROM screened_companies WHERE incorporation_date=?", (date_str,)).fetchall()}
            new_companies = [item for item in companies if item.get("company_number") not in existing]
            st.write(f"Companies found: {len(companies):,}; new enrichments: {len(new_companies):,}")
            progress = st.progress(0)
            failures = []
            for index, item in enumerate(new_companies, start=1):
                try:
                    upsert_company(conn, process_company(client, conn, item, date_str))
                except Exception as exc:
                    failures.append(f"{item.get('company_number')}: {exc}")
                progress.progress(index / max(len(new_companies), 1))
            rebuild_all_associations(conn)
            if failures:
                st.warning("Some records failed to enrich.")
                st.code("\n".join(failures[:50]))
                status.update(label="Completed with errors", state="error")
            else:
                status.update(label="Refresh complete", state="complete")

    if rebuild:
        rebuild_all_associations(conn)
        st.success("Personal shareholder and general owner associations rebuilt.")

    df = pd.read_sql_query("SELECT * FROM screened_companies WHERE incorporation_date=? ORDER BY pulled_at DESC", conn, params=(date_str,))
    display = build_display(df)
    if personal_only:
        display = display[display["Personal Shareholder Company Count"] > 0]
    if associated_only:
        display = display[display["Associated Count"] > 0]
    if shortlisted_only:
        display = display[display["Shortlist"]]
    if name_search.strip():
        display = display[display["Company Name"].str.contains(re.escape(name_search.strip()), case=False, na=False)]
    if sic_search.strip():
        display = display[display["SIC Code"].str.contains(re.escape(sic_search.strip()), case=False, na=False)]
    if hide_mfg:
        display = display[~display["SIC Code"].apply(lambda value: any(code.strip() in MANUFACTURING_WHOLESALE_SIC_CODES for code in str(value).split(",")))]

    st.metric("Visible results", f"{len(display):,}")
    edited = st.data_editor(
        display,
        use_container_width=True,
        hide_index=True,
        disabled=[column for column in display.columns if column != "Shortlist"],
        column_config={
            "Shortlist": st.column_config.CheckboxColumn("Shortlist"),
            "Associated Companies": st.column_config.TextColumn("Associated Companies", width="large"),
            "Personal Shareholders Also In Other Businesses": st.column_config.TextColumn("Personal Shareholders Also In Other Businesses", width="medium"),
            "Personal Shareholder Other Companies": st.column_config.TextColumn("Personal Shareholder Other Companies", width="large"),
            "Personal Shareholder Company Count": st.column_config.NumberColumn("Personal Shareholder Company Count", width="small"),
            "Profile": st.column_config.LinkColumn("Profile", display_text="Open record"),
            "company_number": None,
        },
        key=f"results_{date_str}",
    )
    if not edited.empty:
        for _, row in edited[["company_number", "Shortlist"]].iterrows():
            conn.execute("UPDATE screened_companies SET shortlisted=? WHERE company_number=?", (int(bool(row["Shortlist"])), row["company_number"]))
        conn.commit()
    st.download_button("Download filtered CSV", display.drop(columns=["company_number"], errors="ignore").to_csv(index=False).encode("utf-8"), f"companies_house_personal_shareholders_{date_str}.csv", "text/csv", use_container_width=True)


if __name__ == "__main__":
    main()
    "46520", "46530", "46610", "46620", "46630", "46640", "46650", "46660", "46690", "46711", "46719",
    "46720", "46730", "46740", "46750", "46900",
}
ALL_ALLOWED_SIC_CODES = list({*ALLOWED_SIC_CODES, *MANUFACTURING_WHOLESALE_SIC_CODES})

BONUS_STAR_COUNTRIES = {"sweden", "norway", "united states"}
ALLOWED_COMPANY_TYPES = [
    "ltd", "llp", "private-limited-guarant-nsc", "private-limited-shares-section-30-exemption",
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


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip().lower().replace("-", " ")
    text = re.sub(r"[^a-z0-9\s]", "", text)
    return re.sub(r"\s+", " ", text).strip()


NORMALIZED_COUNTRY_TERMS = {normalize_text(x) for x in COUNTRY_TERMS}
NORMALIZED_ALLOWED_COMPANY_TYPES = {normalize_text(x) for x in ALLOWED_COMPANY_TYPES}


def canonical_country_from_value(value: Any) -> str:
    norm = normalize_text(value)
    if norm == "usa":
        norm = "united states"
    if norm == "united states of america":
        norm = "united states"
    if norm == "the netherlands":
        norm = "netherlands"
    if norm == "hongkong":
        norm = "hong kong"
    if norm in NORMALIZED_COUNTRY_TERMS:
        return norm
    return NATIONALITY_TO_COUNTRY.get(norm, "")


def dedupe_preserve_order(values: List[str]) -> List[str]:
    result: List[str] = []
    seen: Set[str] = set()
    for value in values:
        key = normalize_text(value)
        if key and key not in seen:
            seen.add(key)
            result.append(value)
    return result


def country_label(value: str) -> str:
    return "USA" if value == "united states" else ("Hong Kong" if value == "hong kong" else value.title())


def format_flagged_countries(values: List[str]) -> str:
    countries = dedupe_preserve_order([canonical_country_from_value(v) for v in values])
    return " | ".join(f"✓ {COUNTRY_FLAG_MAP.get(c, '🌍')} {country_label(c)}" for c in countries if c)


def extract_country_flags(values: List[str]) -> List[str]:
    countries = dedupe_preserve_order([canonical_country_from_value(v) for v in values])
    return [COUNTRY_FLAG_MAP[c] for c in countries if c in COUNTRY_FLAG_MAP]


def profile_url(company_number: str, company_name: str) -> str:
    return f"https://find-and-update.company-information.service.gov.uk/company/{company_number}#{quote(company_name or 'company')}"


class CHClient:
    def __init__(self, api_keys: List[str]):
        self.api_keys = [str(k).strip() for k in api_keys if str(k).strip()]
        if not self.api_keys:
            raise ValueError("No Companies House API keys supplied.")
        self.index = 0
        self.session = requests.Session()

    def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        last_error = "unknown error"
        for _ in range(max(len(self.api_keys) * 3, 3)):
            try:
                response = self.session.get(
                    BASE_URL + path,
                    params=params,
                    auth=(self.api_keys[self.index], ""),
                    timeout=30,
                    headers={"Accept": "application/json"},
                )
                if response.status_code == 404:
                    return {}
                if response.status_code in (401, 403, 429):
                    last_error = f"HTTP {response.status_code}"
                    self.index = (self.index + 1) % len(self.api_keys)
                    time.sleep(0.5)
                    continue
                response.raise_for_status()
                return response.json()
            except requests.RequestException as exc:
                last_error = str(exc)
                self.index = (self.index + 1) % len(self.api_keys)
                time.sleep(0.5)
        raise RuntimeError(f"Companies House API request failed after retries: {last_error}")


def paged_get_items(client: CHClient, path: str, page_size: int, extra_params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    start_index = 0
    while True:
        params: Dict[str, Any] = {"start_index": start_index}
        if extra_params:
            params.update(extra_params)
        params["size" if path == "/advanced-search/companies" else "items_per_page"] = page_size
        payload = client.get(path, params=params)
        batch = payload.get("items", []) or []
        result.extend(batch)
        total = int(payload.get("total_results") or payload.get("total_count") or len(result))
        start_index += page_size
        if not batch or start_index >= total:
            break
    return result


def get_all_officers(client: CHClient, company_number: str) -> List[Dict[str, Any]]:
    return paged_get_items(client, f"/company/{company_number}/officers", OFFICERS_PAGE_SIZE)


def get_all_pscs(client: CHClient, company_number: str) -> List[Dict[str, Any]]:
    return paged_get_items(client, f"/company/{company_number}/persons-with-significant-control", PSC_PAGE_SIZE)


def ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        conn.commit()


def init_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS screened_companies (
            company_number TEXT PRIMARY KEY,
            company_name TEXT,
            sic_code TEXT,
            incorporation_date TEXT,
            company_type TEXT,
            international_director INTEGER DEFAULT 0,
            international_shareholder INTEGER DEFAULT 0,
            owned_by_company INTEGER DEFAULT 0,
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
    conn.execute("CREATE INDEX IF NOT EXISTS idx_psc_owner_key ON psc_records(significant_owner_key)")
    conn.commit()
    for column, definition in [
        ("international_director_detail", "TEXT"), ("international_shareholder_detail", "TEXT"),
        ("owner_company_name", "TEXT"), ("profile_url", "TEXT"), ("shortlisted", "INTEGER DEFAULT 0"),
        ("target_sic", "INTEGER DEFAULT 0"), ("target_address", "INTEGER DEFAULT 0"),
        ("target_address_detail", "TEXT"), ("target_indicators", "TEXT"),
        ("associated_company_count", "INTEGER DEFAULT 0"), ("associated_companies", "TEXT"),
    ]:
        ensure_column(conn, "screened_companies", column, definition)
    return conn


def validate_api_keys() -> List[str]:
    if "COMPANIES_HOUSE_API_KEYS" not in st.secrets:
        raise ValueError("Missing COMPANIES_HOUSE_API_KEYS in .streamlit/secrets.toml")
    keys = [str(key).strip() for key in st.secrets["COMPANIES_HOUSE_API_KEYS"] if str(key).strip()]
    if not keys:
        raise ValueError("COMPANIES_HOUSE_API_KEYS is empty")
    return keys


def normalise_company_type(value: Any) -> bool:
    return normalize_text(value) in NORMALIZED_ALLOWED_COMPANY_TYPES


def search_new_companies(client: CHClient, target_date: str) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    params = {
        "incorporated_from": target_date, "incorporated_to": target_date,
        "company_status": "active", "company_type": ",".join(ALLOWED_COMPANY_TYPES),
        "sic_codes": ",".join(ALL_ALLOWED_SIC_CODES),
    }
    raw = paged_get_items(client, "/advanced-search/companies", SEARCH_PAGE_SIZE, params)
    filtered = [
        item for item in raw
        if item.get("company_status", "").lower() == "active"
        and normalise_company_type(item.get("company_type", ""))
        and any(str(code) in ALL_ALLOWED_SIC_CODES for code in (item.get("sic_codes") or []))
    ]
    deduped = {item["company_number"]: item for item in filtered if item.get("company_number")}
    return list(deduped.values()), {"raw": len(raw), "filtered": len(filtered), "deduped": len(deduped)}


def psc_id(psc: Dict[str, Any]) -> str:
    link = str((psc.get("links") or {}).get("self") or "").rstrip("/")
    return link.split("/")[-1] if link else ""


def corporate_company_number(psc: Dict[str, Any]) -> str:
    identification = psc.get("identification") or {}
    for value in [psc.get("company_number"), identification.get("registration_number"), identification.get("company_number")]:
        if value:
            return str(value).strip().upper()
    return ""


def significant_owner_key(psc: Dict[str, Any]) -> str:
    corporate = corporate_company_number(psc)
    if corporate:
        return f"corporate:{corporate}"
    identifier = psc_id(psc)
    if identifier:
        return f"psc:{str(psc.get('kind') or 'unknown').lower()}:{identifier}"
    return f"unmatched:{str(psc.get('kind') or 'unknown').lower()}:{json.dumps(psc, sort_keys=True, default=str)}"


def store_pscs(conn: sqlite3.Connection, company_number: str, pscs: List[Dict[str, Any]]) -> None:
    conn.execute("DELETE FROM psc_records WHERE company_number = ?", (company_number,))
    for psc in pscs:
        conn.execute(
            """
            INSERT OR REPLACE INTO psc_records (
                company_number, significant_owner_key, psc_id, psc_kind, psc_name,
                corporate_company_number, nationality, country_of_residence, notified_on,
                ceased_on, natures_of_control, source_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                company_number, significant_owner_key(psc), psc_id(psc), str(psc.get("kind") or ""),
                str(psc.get("name") or ""), corporate_company_number(psc), str(psc.get("nationality") or ""),
                str(psc.get("country_of_residence") or ""), str(psc.get("notified_on") or ""),
                str(psc.get("ceased_on") or ""), json.dumps(psc.get("natures_of_control") or []), json.dumps(psc),
            ),
        )
    conn.commit()


def upsert_company(conn: sqlite3.Connection, row: Dict[str, Any]) -> None:
    fields = [
        "company_number", "company_name", "sic_code", "incorporation_date", "company_type",
        "international_director", "international_director_detail", "international_shareholder",
        "international_shareholder_detail", "owned_by_company", "owner_company_name", "pulled_at",
        "raw_json", "profile_url", "shortlisted", "target_sic", "target_address", "target_address_detail",
        "target_indicators", "associated_company_count", "associated_companies",
    ]
    values = [
        row["company_number"], row["company_name"], row["sic_code"], row["incorporation_date"], row["company_type"],
        int(row["international_director"]), row.get("international_director_detail", ""),
        int(row["international_shareholder"]), row.get("international_shareholder_detail", ""),
        int(row["owned_by_company"]), row.get("owner_company_name", ""), row["pulled_at"],
        json.dumps(row.get("raw_json", {})), row.get("profile_url", ""), int(row.get("shortlisted", False)),
        int(row.get("target_sic", False)), int(row.get("target_address", False)), row.get("target_address_detail", ""),
        row.get("target_indicators", ""), int(row.get("associated_company_count", 0)), row.get("associated_companies", ""),
    ]
    placeholders = ", ".join("?" for _ in fields)
    updates = ", ".join(f"{field}=excluded.{field}" for field in fields[1:] if field not in {"shortlisted"})
    conn.execute(
        f"INSERT INTO screened_companies ({', '.join(fields)}) VALUES ({placeholders}) "
        f"ON CONFLICT(company_number) DO UPDATE SET {updates}", values,
    )
    conn.commit()


def current_psc_info(psc: Dict[str, Any]) -> Tuple[bool, List[str], bool, List[str]]:
    international_values: List[str] = []
    owner_names: List[str] = []
    kind = str(psc.get("kind") or "")
    for value in [psc.get("country_of_residence"), (psc.get("address") or {}).get("country"), psc.get("nationality")]:
        if canonical_country_from_value(value):
            international_values.append(str(value))
    if kind in COMPANY_OWNER_KINDS or "corporate" in kind or "legal-person" in kind:
        if str(psc.get("name") or "").strip():
            owner_names.append(str(psc["name"]).strip())
    return bool(international_values), dedupe_preserve_order(international_values), bool(owner_names), dedupe_preserve_order(owner_names)


def officer_info(client: CHClient, company_number: str) -> Tuple[bool, List[str], int]:
    values: List[str] = []
    count = 0
    for officer in get_all_officers(client, company_number):
        role = normalize_text(officer.get("officer_role"))
        if "director" not in role and role != "designated member":
            continue
        count += 1
        for value in [officer.get("country_of_residence"), (officer.get("address") or {}).get("country"), officer.get("nationality")]:
            if canonical_country_from_value(value):
                values.append(str(value))
    values = dedupe_preserve_order(values)
    return bool(values), values, count


def process_company(client: CHClient, conn: sqlite3.Connection, item: Dict[str, Any], target_date: str) -> Dict[str, Any]:
    number = str(item.get("company_number") or "")
    name = str(item.get("company_name") or item.get("title") or "")
    intl_director, director_values, director_count = officer_info(client, number)
    pscs = get_all_pscs(client, number)
    store_pscs(conn, number, pscs)
    intl_shareholder = False
    shareholder_values: List[str] = []
    owned = False
    owner_names: List[str] = []
    for psc in pscs:
        if psc.get("ceased_on"):
            continue
        psc_intl, psc_values, psc_owned, psc_names = current_psc_info(psc)
        intl_shareholder |= psc_intl
        shareholder_values.extend(psc_values)
        owned |= psc_owned
        owner_names.extend(psc_names)
    shareholder_values = dedupe_preserve_order(shareholder_values)
    owner_names = dedupe_preserve_order(owner_names)
    target_sic = any(str(code) in TARGET_SIC_CODES for code in (item.get("sic_codes") or []))
    address = item.get("registered_office_address") or item.get("address") or {}
    country = canonical_country_from_value(address.get("country"))
    target_address = bool(country)
    indicators = []
    if target_sic:
        indicators.append("🎯")
    if target_address:
        indicators.append("🏳️")
    indicators.extend(dedupe_preserve_order(extract_country_flags(director_values) + extract_country_flags(shareholder_values)))
    if director_count >= 2:
        indicators.append(f"{director_count} directors")
    stars = sum([intl_director, intl_shareholder, owned, target_sic])
    if has_bonus := bool({canonical_country_from_value(v) for v in director_values + shareholder_values} & BONUS_STAR_COUNTRIES):
        stars += 1
    return {
        "company_number": number, "company_name": name,
        "sic_code": ", ".join(str(x) for x in (item.get("sic_codes") or []) if str(x) in ALL_ALLOWED_SIC_CODES),
        "incorporation_date": target_date, "company_type": item.get("company_type", ""),
        "international_director": intl_director, "international_director_detail": format_flagged_countries(director_values),
        "international_shareholder": intl_shareholder, "international_shareholder_detail": format_flagged_countries(shareholder_values),
        "owned_by_company": owned, "owner_company_name": " | ".join(owner_names),
        "pulled_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"), "raw_json": item,
        "profile_url": profile_url(number, name), "shortlisted": False, "target_sic": target_sic,
        "target_address": target_address,
        "target_address_detail": f"✓ {COUNTRY_FLAG_MAP.get(country, '🌍')} {country_label(country)}" if country else "",
        "target_indicators": " ".join(indicators), "associated_company_count": 0, "associated_companies": "",
    }


def has_bonus_star(values: List[str]) -> bool:
    return bool({canonical_country_from_value(v) for v in values} & BONUS_STAR_COUNTRIES)


def rebuild_associations(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        """
        SELECT p.company_number, p.significant_owner_key, COALESCE(c.company_name, p.company_number)
        FROM psc_records p
        JOIN screened_companies c ON c.company_number = p.company_number
        WHERE COALESCE(p.ceased_on, '') = '' AND p.significant_owner_key NOT LIKE 'unmatched:%'
        """
    ).fetchall()
    groups: Dict[str, Dict[str, str]] = {}
    for company_number, owner_key, company_name in rows:
        groups.setdefault(owner_key, {})[company_number] = company_name
    associations: Dict[str, Dict[str, str]] = {}
    for members in groups.values():
        if len(members) < 2:
            continue
        for number in members:
            associations.setdefault(number, {}).update({n: name for n, name in members.items() if n != number})
    numbers = [row[0] for row in conn.execute("SELECT company_number FROM screened_companies").fetchall()]
    for number in numbers:
        related = associations.get(number, {})
        display = " | ".join(f"{name} ({other})" for other, name in sorted(related.items(), key=lambda x: x[1].lower()))
        conn.execute("UPDATE screened_companies SET associated_company_count=?, associated_companies=? WHERE company_number=?", (len(related), display, number))
    conn.commit()


def read_rows(conn: sqlite3.Connection, incorporation_date: str) -> pd.DataFrame:
    return pd.read_sql_query("SELECT * FROM screened_companies WHERE incorporation_date=? ORDER BY pulled_at DESC", conn, params=(incorporation_date,))


def build_display(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["Shortlist", "Target SIC", "Rating", "Target Indicators", "Company Name", "SIC Code", "Signals", "International Director", "International Shareholder", "Owned By A Company", "Associated Companies", "Associated Count", "Profile", "Pulled At", "company_number"])
    signals = []
    ratings = []
    for _, row in df.iterrows():
        labels = extract_country_flags(str(row.get("international_director_detail", "")).split("|")) + extract_country_flags(str(row.get("international_shareholder_detail", "")).split("|"))
        if str(row.get("owner_company_name", "")).strip():
            labels.append("🏢")
        if int(row.get("associated_company_count", 0) or 0) > 0:
            labels.append("🔗")
        signals.append(" ".join(dedupe_preserve_order(labels)))
        stars = sum(bool(row.get(key, 0)) for key in ["international_director", "international_shareholder", "owned_by_company", "target_sic"])
        if has_bonus_star(str(row.get("international_director_detail", "")).split("|") + str(row.get("international_shareholder_detail", "")).split("|")):
            stars += 1
        ratings.append("⭐" * stars)
    return pd.DataFrame({
        "Shortlist": df["shortlisted"].fillna(0).astype(bool),
        "Target SIC": df["target_sic"].fillna(0).map(lambda x: "🎯" if x else ""),
        "Rating": ratings, "Target Indicators": df["target_indicators"].fillna(""), "Company Name": df["company_name"],
        "SIC Code": df["sic_code"], "Signals": signals, "International Director": df["international_director_detail"].fillna(""),
        "International Shareholder": df["international_shareholder_detail"].fillna(""), "Owned By A Company": df["owner_company_name"].fillna(""),
        "Associated Companies": df["associated_companies"].fillna(""), "Associated Count": df["associated_company_count"].fillna(0).astype(int),
        "Profile": df["profile_url"].fillna(""), "Pulled At": df["pulled_at"], "company_number": df["company_number"],
    })


def main() -> None:
    st.title("Companies House New Incorporations Screener")
    st.caption("Screen newly incorporated companies and identify associated companies using Significant Owner keys.")
    try:
        keys = validate_api_keys()
    except Exception as exc:
        st.error(str(exc))
        st.stop()
    conn = init_db()
    client = CHClient(keys)
    with st.sidebar:
        target_date = st.date_input("Incorporation date", value=date.today(), format="YYYY-MM-DD")
        run = st.button("Pull new companies", type="primary", use_container_width=True)
        rebuild = st.button("Rebuild associated companies", use_container_width=True)
        only_associated = st.checkbox("Show associated companies only")
        only_shortlisted = st.checkbox("Show shortlisted only")
        hide_mfg = st.checkbox("Hide Manufacturing & Wholesale SICs")
        company_search = st.text_input("Filter by company name")
        sic_search = st.text_input("Filter by SIC code")
    date_str = target_date.strftime("%Y-%m-%d")
    if run:
        with st.status("Running Companies House screening...", expanded=True) as status:
            companies, diagnostics = search_new_companies(client, date_str)
            existing = {row[0] for row in conn.execute("SELECT company_number FROM screened_companies WHERE incorporation_date=?", (date_str,)).fetchall()}
            new_companies = [item for item in companies if item.get("company_number") not in existing]
            st.write(f"Raw results: {diagnostics['raw']:,}; retained: {diagnostics['filtered']:,}; new: {len(new_companies):,}")
            progress = st.progress(0)
            failures = []
            for index, item in enumerate(new_companies, start=1):
                try:
                    upsert_company(conn, process_company(client, conn, item, date_str))
                except Exception as exc:
                    failures.append(f"{item.get('company_number')}: {exc}")
                progress.progress(index / max(len(new_companies), 1))
            rebuild_associations(conn)
            if failures:
                st.warning("Some companies failed to enrich.")
                st.code("\n".join(failures[:50]))
                status.update(label="Completed with errors", state="error")
            else:
                status.update(label="Refresh complete", state="complete")
    if rebuild:
        rebuild_associations(conn)
        st.success("Associated companies rebuilt.")
    display = build_display(read_rows(conn, date_str))
    if only_associated:
        display = display[display["Associated Count"] > 0]
    if only_shortlisted:
        display = display[display["Shortlist"]]
    if company_search.strip():
        display = display[display["Company Name"].str.contains(re.escape(company_search.strip()), case=False, na=False)]
    if sic_search.strip():
        display = display[display["SIC Code"].str.contains(re.escape(sic_search.strip()), case=False, na=False)]
    if hide_mfg:
        display = display[~display["SIC Code"].apply(lambda value: any(code.strip() in MANUFACTURING_WHOLESALE_SIC_CODES for code in str(value).split(",")))]
    st.metric("Visible results", f"{len(display):,}")
    editor = st.data_editor(
        display,
        use_container_width=True,
        hide_index=True,
        disabled=[column for column in display.columns if column != "Shortlist"],
        column_config={
            "Shortlist": st.column_config.CheckboxColumn("Shortlist"),
            "Associated Companies": st.column_config.TextColumn("Associated Companies", width="large"),
            "Profile": st.column_config.LinkColumn("Profile", display_text="Open record"),
            "company_number": None,
        },
        key=f"results_{date_str}",
    )
    if not editor.empty:
        for _, row in editor[["company_number", "Shortlist"]].iterrows():
            conn.execute("UPDATE screened_companies SET shortlisted=? WHERE company_number=?", (int(bool(row["Shortlist"])), row["company_number"]))
        conn.commit()
    st.download_button("Download filtered CSV", display.drop(columns=["company_number"], errors="ignore").to_csv(index=False).encode("utf-8"), f"companies_house_{date_str}.csv", "text/csv", use_container_width=True)


if __name__ == "__main__":
    main()
