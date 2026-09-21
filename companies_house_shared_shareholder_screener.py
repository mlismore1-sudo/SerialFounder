import json
import re
import time
from datetime import date
from typing import Any, Dict, List, Optional, Set
from urllib.parse import quote

import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="Companies House Shared Shareholder Screener", layout="wide")

BASE_URL = "https://api.company-information.service.gov.uk"
SEARCH_PAGE_SIZE = 5000
PAGE_SIZE = 100
ALLOWED_COMPANY_TYPES = ["ltd", "llp", "private-limited-guarant-nsc", "private-limited-shares-section-30-exemption"]
ALLOWED_SIC_CODES = [
    "62012", "62020", "63120", "47910", "46190", "46499", "70229", "73110", "74909", "68209",
    "64209", "68100", "32990", "10890", "86900", "93130", "96040", "82990", "72110", "56101",
]
TARGET_SIC_CODES = {"62012", "72110", "56101"}
MANUFACTURING_WHOLESALE_SIC_CODES = {
    "10110", "10130", "10310", "10410", "10511", "10512", "10611", "10612", "10840", "10850", "10890",
    "10920", "13100", "10300", "13921", "13923", "13960", "14131", "15110", "16290", "19200",
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
ALL_ALLOWED_SIC_CODES = sorted(set(ALLOWED_SIC_CODES) | MANUFACTURING_WHOLESALE_SIC_CODES)
COUNTRIES = {"usa", "united states", "france", "germany", "belgium", "norway", "sweden", "finland", "denmark", "austria", "poland", "spain", "portugal", "greece", "italy", "hungary", "croatia", "ireland", "china", "netherlands", "india", "hong kong", "singapore"}
NATIONALITIES = {"american": "united states", "us": "united states", "french": "france", "german": "germany", "belgian": "belgium", "norwegian": "norway", "swedish": "sweden", "finnish": "finland", "danish": "denmark", "austrian": "austria", "polish": "poland", "spanish": "spain", "portuguese": "portugal", "greek": "greece", "italian": "italy", "hungarian": "hungary", "croatian": "croatia", "irish": "ireland", "chinese": "china", "indian": "india", "dutch": "netherlands", "singaporean": "singapore"}
FLAGS = {"united states": "🇺🇸", "france": "🇫🇷", "germany": "🇩🇪", "belgium": "🇧🇪", "norway": "🇳🇴", "sweden": "🇸🇪", "finland": "🇫🇮", "denmark": "🇩🇰", "austria": "🇦🇹", "poland": "🇵🇱", "spain": "🇪🇸", "portugal": "🇵🇹", "greece": "🇬🇷", "italy": "🇮🇹", "hungary": "🇭🇺", "croatia": "🇭🇷", "ireland": "🇮🇪", "china": "🇨🇳", "netherlands": "🇳🇱", "india": "🇮🇳", "hong kong": "🇭🇰", "singapore": "🇸🇬"}
CORPORATE_KINDS = {"corporate-entity-person-with-significant-control", "legal-person-person-with-significant-control", "super-secure-person-with-significant-control"}


def clean(value: Any) -> str:
    text = str(value or "").strip().lower().replace("-", " ")
    text = re.sub(r"[^a-z0-9\s]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def get_country(value: Any) -> str:
    text = clean(value)
    aliases = {"usa": "united states", "united states of america": "united states", "hongkong": "hong kong"}
    text = aliases.get(text, text)
    if text in COUNTRIES:
        return text
    return NATIONALITIES.get(text, "")


def unique(values: List[str]) -> List[str]:
    result: List[str] = []
    seen: Set[str] = set()
    for value in values:
        key = clean(value)
        if key and key not in seen:
            seen.add(key)
            result.append(value)
    return result


def country_text(values: List[str]) -> str:
    countries = unique([get_country(value) for value in values])
    return " | ".join(f"✓ {FLAGS.get(value, '🌍')} {'USA' if value == 'united states' else value.title()}" for value in countries if value)


def profile_url(number: str, name: str) -> str:
    return f"https://find-and-update.company-information.service.gov.uk/company/{number}#{quote(name or 'company')}"


class CH:
    def __init__(self, keys: List[str]):
        self.keys = [str(key).strip() for key in keys if str(key).strip()]
        self.index = 0
        self.session = requests.Session()

    def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        last = "unknown error"
        for _ in range(max(3, len(self.keys) * 3)):
            try:
                response = self.session.get(BASE_URL + path, params=params, auth=(self.keys[self.index], ""), timeout=30)
                if response.status_code == 404:
                    return {}
                if response.status_code in (401, 403, 429):
                    last = f"HTTP {response.status_code}"
                    self.index = (self.index + 1) % len(self.keys)
                    time.sleep(0.5)
                    continue
                response.raise_for_status()
                return response.json()
            except requests.RequestException as exc:
                last = str(exc)
                self.index = (self.index + 1) % len(self.keys)
                time.sleep(0.5)
        raise RuntimeError(f"Companies House request failed: {last}")


def page(client: CH, path: str, params: Optional[Dict[str, Any]] = None, size: int = PAGE_SIZE) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    start = 0
    while True:
        query = {"start_index": start}
        if params:
            query.update(params)
        query["size" if path == "/advanced-search/companies" else "items_per_page"] = size
        payload = client.get(path, query)
        batch = payload.get("items", []) or []
        result.extend(batch)
        total = int(payload.get("total_results") or payload.get("total_count") or len(result))
        start += size
        if not batch or start >= total:
            return result


def psc_key(psc: Dict[str, Any]) -> str:
    identification = psc.get("identification") or {}
    corporate = psc.get("company_number") or identification.get("registration_number") or identification.get("company_number")
    if corporate:
        return f"corporate:{str(corporate).strip().upper()}"
    self_link = str((psc.get("links") or {}).get("self") or "").rstrip("/")
    identifier = self_link.split("/")[-1] if self_link else ""
    if identifier:
        return f"psc:{str(psc.get('kind') or 'unknown').lower()}:{identifier}"
    return f"unmatched:{json.dumps(psc, sort_keys=True, default=str)}"


def individual_psc(psc: Dict[str, Any]) -> bool:
    kind = str(psc.get("kind") or "").lower()
    return "individual" in kind and "corporate" not in kind


def enrich(client: CH, item: Dict[str, Any]) -> Dict[str, Any]:
    number = str(item.get("company_number") or "")
    name = str(item.get("company_name") or item.get("title") or "")
    officers = page(client, f"/company/{number}/officers")
    pscs = page(client, f"/company/{number}/persons-with-significant-control")
    director_countries: List[str] = []
    director_count = 0
    for officer in officers:
        role = clean(officer.get("officer_role"))
        if "director" not in role and role != "designated member":
            continue
        director_count += 1
        director_countries.extend(str(value) for value in [officer.get("country_of_residence"), (officer.get("address") or {}).get("country"), officer.get("nationality")] if get_country(value))
    shareholder_countries: List[str] = []
    parent_names: List[str] = []
    current_pscs: List[Dict[str, Any]] = []
    for psc in pscs:
        if psc.get("ceased_on"):
            continue
        current_pscs.append(psc)
        shareholder_countries.extend(str(value) for value in [psc.get("country_of_residence"), (psc.get("address") or {}).get("country"), psc.get("nationality")] if get_country(value))
        kind = str(psc.get("kind") or "")
        if kind in CORPORATE_KINDS or "corporate" in kind or "legal-person" in kind:
            if str(psc.get("name") or "").strip():
                parent_names.append(str(psc["name"]).strip())
    director_countries = unique(director_countries)
    shareholder_countries = unique(shareholder_countries)
    parent_names = unique(parent_names)
    address = item.get("registered_office_address") or item.get("address") or {}
    address_country = get_country(address.get("country"))
    target_sic = any(str(code) in TARGET_SIC_CODES for code in item.get("sic_codes", []))
    indicators = []
    if target_sic:
        indicators.append("🎯")
    if address_country:
        indicators.append("🏳️")
    if director_count >= 2:
        indicators.append(f"{director_count} directors")
    stars = sum(bool(value) for value in [director_countries, shareholder_countries, parent_names, target_sic])
    if {get_country(value) for value in director_countries + shareholder_countries} & {"sweden", "norway", "united states"}:
        stars += 1
    return {
        "company_number": number,
        "company_name": name,
        "company_type": item.get("company_type", ""),
        "incorporation_date": item.get("date_of_creation", ""),
        "sic_code": ", ".join(str(code) for code in item.get("sic_codes", []) if str(code) in ALL_ALLOWED_SIC_CODES),
        "profile": profile_url(number, name),
        "pscs": current_pscs,
        "director_detail": country_text(director_countries),
        "shareholder_detail": country_text(shareholder_countries),
        "parent_names": " | ".join(parent_names),
        "target_sic": target_sic,
        "indicators": " ".join(indicators),
        "rating": "⭐" * stars,
        "shortlist": False,
    }


def add_associations(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    groups: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for record in records:
        for psc in record.get("pscs", []):
            key = psc_key(psc)
            if key.startswith("unmatched:"):
                continue
            groups.setdefault(key, {})[record["company_number"]] = {
                "name": record["company_name"],
                "owner": str(psc.get("name") or key),
                "individual": individual_psc(psc),
            }
    output: List[Dict[str, Any]] = []
    for record in records:
        number = record["company_number"]
        all_related: Dict[str, str] = {}
        personal_people: Set[str] = set()
        personal_related: Dict[str, str] = {}
        for members in groups.values():
            if number not in members or len(members) < 2:
                continue
            current = members[number]
            for other_number, other in members.items():
                if other_number == number:
                    continue
                all_related[other_number] = other["name"]
                if current["individual"]:
                    personal_people.add(current["owner"])
                    personal_related[other_number] = other["name"]
        copy = dict(record)
        copy["associated_count"] = len(all_related)
        copy["associated_companies"] = " | ".join(f"{name} ({other})" for other, name in sorted(all_related.items(), key=lambda item: item[1].lower()))
        copy["personal_people"] = " | ".join(sorted(personal_people, key=str.lower))
        copy["personal_companies"] = " | ".join(f"{name} ({other})" for other, name in sorted(personal_related.items(), key=lambda item: item[1].lower()))
        copy["personal_count"] = len(personal_related)
        output.append(copy)
    return output


def dataframe(records: List[Dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for record in records:
        signals = []
        if record.get("parent_names"):
            signals.append("🏢")
        if record.get("associated_count"):
            signals.append("🔗")
        if record.get("personal_count"):
            signals.append("👤")
        rows.append({
            "Shortlist": record.get("shortlist", False),
            "Target SIC": "🎯" if record.get("target_sic") else "",
            "Rating": record.get("rating", ""),
            "Target Indicators": record.get("indicators", ""),
            "Company Name": record.get("company_name", ""),
            "SIC Code": record.get("sic_code", ""),
            "Signals": " ".join(signals),
            "International Director": record.get("director_detail", ""),
            "International Shareholder": record.get("shareholder_detail", ""),
            "Owned By A Company": record.get("parent_names", ""),
            "Associated Companies": record.get("associated_companies", ""),
            "Associated Count": record.get("associated_count", 0),
            "Personal Shareholders Also In Other Businesses": record.get("personal_people", ""),
            "Personal Shareholder Other Companies": record.get("personal_companies", ""),
            "Personal Shareholder Company Count": record.get("personal_count", 0),
            "Profile": record.get("profile", ""),
            "Incorporation Date": record.get("incorporation_date", ""),
            "company_number": record.get("company_number", ""),
        })
    return pd.DataFrame(rows)


def main() -> None:
    st.title("Companies House Shared Shareholder Screener")
    st.caption("Streamlit-only version. No SQL database and no local persistence.")
    st.info("Results remain in memory for this Streamlit session only.")
    if "records" not in st.session_state:
        st.session_state.records = []
    try:
        keys = [str(value).strip() for value in st.secrets["COMPANIES_HOUSE_API_KEYS"] if str(value).strip()]
    except Exception:
        st.error("Add COMPANIES_HOUSE_API_KEYS to .streamlit/secrets.toml")
        st.stop()
    if not keys:
        st.error("COMPANIES_HOUSE_API_KEYS is empty")
        st.stop()
    client = CH(keys)
    with st.sidebar:
        selected_date = st.date_input("Incorporation date", value=date.today(), format="YYYY-MM-DD")
        pull = st.button("Pull new companies", type="primary", use_container_width=True)
        clear = st.button("Clear results", use_container_width=True)
        personal_only = st.checkbox("Show shared personal shareholders only")
        associated_only = st.checkbox("Show associated companies only")
        hide_mfg = st.checkbox("Hide Manufacturing & Wholesale SICs")
        name_filter = st.text_input("Filter by company name")
        sic_filter = st.text_input("Filter by SIC code")
    if clear:
        st.session_state.records = []
        st.rerun()
    if pull:
        selected_date_text = selected_date.strftime("%Y-%m-%d")
        with st.status("Pulling Companies House records...", expanded=True) as status:
            companies = search_new_companies(client, selected_date_text)
            existing = {record["company_number"] for record in st.session_state.records}
            new_items = [item for item in companies if item.get("company_number") not in existing]
            st.write(f"Companies found: {len(companies):,}; new records: {len(new_items):,}")
            progress = st.progress(0)
            failures = []
            for index, item in enumerate(new_items, start=1):
                try:
                    st.session_state.records.append(enrich(client, item))
                except Exception as exc:
                    failures.append(f"{item.get('company_number')}: {exc}")
                progress.progress(index / max(len(new_items), 1))
            st.session_state.records = add_associations(st.session_state.records)
            if failures:
                st.warning("Some records failed to enrich.")
                st.code("\n".join(failures[:50]))
                status.update(label="Completed with errors", state="error")
            else:
                status.update(label="Refresh complete", state="complete")
    if st.session_state.records:
        st.session_state.records = add_associations(st.session_state.records)
    records = list(st.session_state.records)
    if personal_only:
        records = [record for record in records if record.get("personal_count", 0) > 0]
    if associated_only:
        records = [record for record in records if record.get("associated_count", 0) > 0]
    if name_filter.strip():
        records = [record for record in records if normalise(name_filter) in normalise(record.get("company_name"))]
    if sic_filter.strip():
        records = [record for record in records if sic_filter.strip() in str(record.get("sic_code", ""))]
    if hide_mfg:
        records = [record for record in records if not any(code.strip() in MANUFACTURING_WHOLESALE_SIC_CODES for code in str(record.get("sic_code", "")).split(","))]
    table = dataframe(records)
    st.metric("Visible results", f"{len(table):,}")
    edited = st.data_editor(table, use_container_width=True, hide_index=True, disabled=[column for column in table.columns if column != "Shortlist"], column_config={
        "Shortlist": st.column_config.CheckboxColumn("Shortlist"),
        "Associated Companies": st.column_config.TextColumn("Associated Companies", width="large"),
        "Personal Shareholders Also In Other Businesses": st.column_config.TextColumn("Personal Shareholders Also In Other Businesses", width="medium"),
        "Personal Shareholder Other Companies": st.column_config.TextColumn("Personal Shareholder Other Companies", width="large"),
        "Personal Shareholder Company Count": st.column_config.NumberColumn("Personal Shareholder Company Count", width="small"),
        "Profile": st.column_config.LinkColumn("Profile", display_text="Open record"),
        "company_number": None,
    }, key=f"results_{selected_date}")
    if not edited.empty:
        shortlist = {row["company_number"]: bool(row["Shortlist"]) for _, row in edited.iterrows()}
        for record in st.session_state.records:
            if record["company_number"] in shortlist:
                record["shortlist"] = shortlist[record["company_number"]]
    st.download_button("Download filtered CSV", table.drop(columns=["company_number"], errors="ignore").to_csv(index=False).encode("utf-8"), f"companies_house_shared_shareholders_{selected_date}.csv", "text/csv", use_container_width=True)


if __name__ == "__main__":
    main()
