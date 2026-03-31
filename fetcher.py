import argparse
import os
import sys
import json
import time
import re
from typing import Dict, List, Optional, Tuple

import pandas as pd
import requests
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from geopy.distance import geodesic
from geopy.geocoders import Nominatim as GeopyNominatim
from thefuzz import fuzz

load_dotenv()

DEFAULT_ADDRESS = "Persiaran Tebar Layar, Bukit Jelutong, 40150 Shah Alam, Selangor"
DEFAULT_LAT = 3.1022
DEFAULT_LNG = 101.5333
DEFAULT_RADIUS_KM = 10

ELMINA_LAT = 3.1500
ELMINA_LNG = 101.4800
ELMINA_RADIUS_METERS = 4000

ECE_SYNONYMS = [
    "tadika", "taska", "preschool", "kindergarten",
    "playschool", "educare", "childcare",
    "pusat jagaan", "pusat perkembangan",
]

JUNK_PATTERNS = [
    r"preschool/kindergarten in \w",
    r"selected preschools in",
    r"best \d+ preschools",
    r"most prestigious",
    r"selected international preschools",
    r"^read also:",
    r"preschool in \w+ \| fees",
]

NON_ECE_KEYWORDS = [
    "tuition", "tuisyen", "academy for teens", "secondary", "high school",
    "college", "university", "clinic", "hospital", "restaurant", "gym",
]

IGNORE_EXACT_WORDS = [
    "tadika", "taska", "preschool", "kindergarten", "transit", "childcare", "lain-lain"
]

RAW_COLUMNS = [
    "centre_name", "address", "neighbourhood", "lat", "lng",
    "curriculum", "language_medium", "fee_halfday_raw",
    "fee_fullday_raw", "scale", "religious_orientation",
    "is_moe_registered", "source_primary", "source_notes",
]


def log(msg: str) -> None:
    print(msg)


def safe_text(val: Optional[str], default: str = "") -> str:
    if val is None:
        return default
    return str(val).strip()


def geocode_address(address: str) -> Tuple[float, float, bool]:
    geolocator = GeopyNominatim(user_agent="project-kestrel-fetcher")
    try:
        loc = geolocator.geocode(address, timeout=10)
        if loc:
            return float(loc.latitude), float(loc.longitude), True
    except Exception as exc:
        log(f"Geocoding failed for '{address}': {exc}")
    return DEFAULT_LAT, DEFAULT_LNG, False


def is_within_radius(centre_lat: float, centre_lng: float, point_lat: float, point_lng: float, radius_km: float = 10) -> bool:
    centre = (centre_lat, centre_lng)
    point = (point_lat, point_lng)
    return geodesic(centre, point).km <= radius_km


def blank_record() -> Dict:
    return {k: ("" if k not in ["lat", "lng", "is_moe_registered"] else (False if k == "is_moe_registered" else None)) for k in RAW_COLUMNS}


def merge_records(existing: Dict, new: Dict) -> Dict:
    for key in RAW_COLUMNS:
        if key == "is_moe_registered":
            existing[key] = bool(existing.get(key, False) or new.get(key, False))
            continue
        if not safe_text(existing.get(key)) and safe_text(new.get(key)):
            existing[key] = new[key]

    existing_source = safe_text(existing.get("source_notes"))
    new_source = safe_text(new.get("source_notes"))
    if new_source and new_source not in existing_source:
        existing["source_notes"] = (existing_source + " | " + new_source).strip(" |")
    return existing


def deduplicate(records: List[Dict]) -> List[Dict]:
    seen = []
    for record in records:
        duplicate_found = False
        for idx, existing in enumerate(seen):
            name_sim = fuzz.token_sort_ratio(record["centre_name"], existing["centre_name"])
            if record.get("lat") is not None and existing.get("lat") is not None:
                dist = geodesic((record["lat"], record["lng"]), (existing["lat"], existing["lng"])).meters
            else:
                dist = 999999

            if name_sim > 85 and dist < 300:
                seen[idx] = merge_records(existing, record)
                duplicate_found = True
                break
            elif name_sim > 92 and record.get("lat") is None and existing.get("lat") is None:
                seen[idx] = merge_records(existing, record)
                duplicate_found = True
                break
        if not duplicate_found:
            seen.append(record)
    return seen


def is_junk_name(name: str) -> bool:
    name_lower = safe_text(name).lower().strip()
    return any(re.search(p, name_lower) for p in JUNK_PATTERNS)


def is_non_ece(name: str) -> bool:
    name_lower = safe_text(name).lower()
    return any(kw in name_lower for kw in NON_ECE_KEYWORDS)


def fetch_overpass(centre_lat: float, centre_lng: float, radius_km: float) -> List[Dict]:
    log("[Overpass] Fetching OpenStreetMap Data...")
    out = []

    mirrors = [
        "https://overpass-api.de/api/interpreter",
        "https://overpass.kumi.systems/api/interpreter",
        "https://overpass.openstreetmap.fr/api/interpreter"
    ]

    headers = {'User-Agent': 'ProjectKestrelBot/1.0 (mailto:data@example.com)'}
    queries = [
        (centre_lat, centre_lng, int(radius_km * 1000), "main"),
        (ELMINA_LAT, ELMINA_LNG, ELMINA_RADIUS_METERS, "elmina"),
    ]

    for q_lat, q_lng, q_radius, label in queries:
        overpass_ql = f"""
        [out:json][timeout:25];
        (
          node["amenity"~"kindergarten|childcare"](around:{q_radius},{q_lat},{q_lng});
          way["amenity"~"kindergarten|childcare"](around:{q_radius},{q_lat},{q_lng});
        );
        out center;
        """.strip()

        success = False
        for mirror in mirrors:
            if success:
                break
            log(f"[Overpass] Trying mirror: {mirror.split('//')[1].split('/')[0]} for {label}...")
            try:
                resp = requests.post(mirror, data={'data': overpass_ql}, headers=headers, timeout=20)
                resp.raise_for_status()
                payload = resp.json()
                for el in payload.get("elements", []):
                    lat = el.get("lat") or el.get("center", {}).get("lat")
                    lng = el.get("lon") or el.get("center", {}).get("lon")
                    if lat is None or lng is None:
                        continue

                    rec = blank_record()
                    tags = el.get("tags", {})
                    rec["centre_name"] = safe_text(tags.get("name"), "Unnamed ECE Centre")
                    rec["address"] = ", ".join(filter(None, [
                        safe_text(tags.get("addr:housenumber")),
                        safe_text(tags.get("addr:street")),
                        safe_text(tags.get("addr:city"))
                    ])).strip(", ")
                    rec["lat"] = float(lat)
                    rec["lng"] = float(lng)
                    rec["source_primary"] = "Overpass API"
                    rec["source_notes"] = f"[{label} area]"
                    out.append(rec)
                success = True
            except Exception as exc:
                log(f"[Overpass] Failed on mirror {mirror}: {exc}")

    log(f"[Overpass] Collected {len(out)} rows")
    return out


def fetch_osm_nominatim() -> List[Dict]:
    log("[Nominatim] Fetching OpenStreetMap Geocoding Data...")
    out = []
    headers = {'User-Agent': 'ProjectKestrelBot/1.0 (mailto:data@example.com)'}

    queries = ["kindergarten in Shah Alam", "preschool in Shah Alam", "tadika in Shah Alam"]

    for q in queries:
        try:
            url = "https://nominatim.openstreetmap.org/search"
            params = {"q": q, "format": "json", "addressdetails": 1, "limit": 40}
            resp = requests.get(url, params=params, headers=headers, timeout=15)
            resp.raise_for_status()

            for item in resp.json():
                rec = blank_record()
                rec["centre_name"] = item.get("name", "").split(",")[0]
                if not rec["centre_name"] or len(rec["centre_name"]) < 4:
                    continue

                rec["address"] = item.get("display_name", "")
                rec["lat"] = float(item.get("lat"))
                rec["lng"] = float(item.get("lon"))
                rec["source_primary"] = "Nominatim API"
                out.append(rec)
            time.sleep(1)
        except Exception as e:
            log(f"[Nominatim] Failed for query '{q}': {e}")

    log(f"[Nominatim] Collected {len(out)} rows")
    return out


def fetch_data_gov_my() -> int:
    log("[data.gov.my] Fetching summary...")
    try:
        url = "https://storage.data.gov.my/education/schools_district.csv"
        df = pd.read_csv(url)
        df_filtered = df[(df['state'] == 'Selangor') & (df['district'] == 'Petaling')].copy()
        total = int(df_filtered['schools'].sum())
        os.makedirs("data", exist_ok=True)
        with open("data/gov_summary.json", "w") as f:
            json.dump({"total_gov_schools_petaling": total}, f, indent=4)
        log(f"[data.gov.my] Total government schools in Petaling: {total}")
        return total
    except Exception as exc:
        log(f"[data.gov.my] Fetch failed: {exc}. Continuing.")
        return 0


def fetch_local_directories() -> List[Dict]:
    log("[Directories] Fetching Kiddy123, Anak2U, GogoKids, and EduDestination natively via Playwright...")
    out = []

    targets = [
        ("Kiddy123", "https://www.kiddy123.com/article/selected-preschools-in-shah-alam"),
        ("Kiddy123", "https://www.kiddy123.com/article/selected-preschools-in-setia-alam"),
        ("Anak2U", "https://explore.anak2u.com.my/negeri/selangor/Shah-Alam-14"),
        ("Anak2U", "https://explore.anak2u.com.my/negeri/selangor/Setia-Alam-2"),
        ("GogoKids", "https://www.gogokids.my/kindergarten/selangor/"),
        ("EduDestination", "https://educationdestinationmalaysia.com/schools/preschool-kindergarten/selangor")
    ]

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
            page = context.new_page()

            for source, url in targets:
                try:
                    log(f"[{source}] Scraping: {url.split('/')[-1] or 'selangor'}")
                    page.goto(url, wait_until="domcontentloaded", timeout=20000)
                    soup = BeautifulSoup(page.content(), "html.parser")

                    for a in soup.select("a"):
                        text = a.get_text(" ", strip=True)
                        href = a.get("href", "")

                        if not text or len(text) < 5 or text.lower() in IGNORE_EXACT_WORDS:
                            continue

                        is_valid = False

                        if source == "Kiddy123" and "kiddy123.com" in href and any(t in text.lower() for t in ECE_SYNONYMS):
                            is_valid = True
                        elif source == "Anak2U" and any(t in text.lower() for t in ECE_SYNONYMS):
                            is_valid = True
                        elif source == "GogoKids" and "school" in href.lower() and any(t in text.lower() for t in ECE_SYNONYMS):
                            is_valid = True
                        elif source == "EduDestination" and any(t in text.lower() for t in ECE_SYNONYMS):
                            is_valid = True

                        if is_valid:
                            rec = blank_record()
                            rec["centre_name"] = text
                            rec["source_primary"] = source
                            out.append(rec)

                except Exception as e:
                    log(f"[{source}] Failed to parse {url}: {e}")
            browser.close()
    except Exception as e:
        log(f"[Directories] Playwright failed: {e}")

    log(f"[Directories] Collected {len(out)} rows")
    return out


def fetch_serpapi() -> List[Dict]:
    key = os.getenv("SERPAPI_KEY")
    if not key:
        log("[SerpAPI] Key not found — skipping.")
        return []

    log("[SerpAPI] Fetching Google Search results...")
    out = []
    endpoint = "https://serpapi.com/search.json"
    queries = ["tadika Bukit Jelutong", "preschool Setia Alam", "taska Elmina Shah Alam", "childcare Glenmarie", "kindergarten Denai Alam"]

    for q in queries:
        params = {"engine": "google", "q": q, "api_key": key, "num": 10}
        try:
            log(f"[SerpAPI] Querying: {q}")
            resp = requests.get(endpoint, params=params, timeout=30)
            resp.raise_for_status()
            results = resp.json().get("organic_results", [])
            log(f"[SerpAPI] Found {len(results)} results for '{q}'")

            for result in results:
                raw_title = safe_text(result.get("title"))
                candidate_name = safe_text(raw_title).split("-")[0].strip()
                candidate_name = re.sub(
                    r"^(tadika|taska|preschool|kindergarten|playschool|educare|childcare|pusat jagaan|pusat perkembangan)\\s*",
                    "",
                    candidate_name,
                    flags=re.IGNORECASE,
                ).strip()
                candidate_name = candidate_name.strip("() ").strip()

                if is_junk_name(raw_title) or is_junk_name(candidate_name):
                    log(f"Rejected junk entry: {raw_title}")
                    continue

                if not candidate_name or len(candidate_name) < 5 or candidate_name in {"()", ""}:
                    log(f"Skipped unnamed entry from SerpAPI: {raw_title}")
                    continue

                rec = blank_record()
                rec["centre_name"] = candidate_name
                rec["address"] = safe_text(result.get("snippet"))
                rec["source_primary"] = "SerpAPI"
                out.append(rec)
        except Exception as exc:
            log(f"[SerpAPI] Failed for '{q}': {exc}")

    log(f"[SerpAPI] Collected {len(out)} rows")
    return out


def validate_radius(records: List[Dict], centre_lat: float, centre_lng: float, radius_km: float) -> Tuple[List[Dict], int]:
    kept, excluded = [], 0
    for rec in records:
        lat, lng = rec.get("lat"), rec.get("lng")
        if lat is None or lng is None:
            kept.append(rec)
        elif is_within_radius(centre_lat, centre_lng, float(lat), float(lng), radius_km):
            kept.append(rec)
        else:
            excluded += 1
    return kept, excluded


def ensure_schema(df: pd.DataFrame) -> pd.DataFrame:
    for col in RAW_COLUMNS:
        if col not in df.columns:
            df[col] = "" if col not in ["lat", "lng", "is_moe_registered"] else None
    return df[RAW_COLUMNS]


def run_pipeline(address: str, radius_km: float, output_csv: str) -> None:
    centre_lat, centre_lng, ok = geocode_address(address)
    log(f"Resolved centre: {centre_lat:.4f}, {centre_lng:.4f}" if ok else "Geocoding failed.")

    all_records = []

    all_records.extend(fetch_overpass(centre_lat, centre_lng, radius_km))
    all_records.extend(fetch_osm_nominatim())

    fetch_data_gov_my()

    all_records.extend(fetch_local_directories())

    all_records.extend(fetch_serpapi())

    filtered_records = []
    for rec in all_records:
        name = safe_text(rec.get("centre_name"))
        if is_non_ece(name):
            log(f"Filtered non-ECE entry: {name}")
            continue
        filtered_records.append(rec)
    all_records = filtered_records

    deduped = deduplicate(all_records)
    within_radius, excluded = validate_radius(deduped, centre_lat, centre_lng, radius_km)

    df = ensure_schema(pd.DataFrame(within_radius))

    os.makedirs(os.path.dirname(output_csv), exist_ok=True)
    df.to_csv(output_csv, index=False)

    log(f"\nSUCCESS: Written {len(df)} rows to {output_csv}")
    log(f"Excluded {excluded} out-of-bounds rows.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--address", default=DEFAULT_ADDRESS)
    parser.add_argument("--radius", type=float, default=DEFAULT_RADIUS_KM)
    parser.add_argument("--output", default="data/raw_ece_data.csv")
    args = parser.parse_args()
    run_pipeline(args.address, args.radius, args.output)
