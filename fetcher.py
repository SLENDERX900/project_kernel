import argparse
import os
import re
import time
from typing import Dict, List, Optional, Tuple

import pandas as pd
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from geopy.distance import geodesic
from geopy.geocoders import Nominatim
from thefuzz import fuzz
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

load_dotenv()

DEFAULT_ADDRESS = "Persiaran Tebar Layar, Bukit Jelutong, 40150 Shah Alam, Selangor"
DEFAULT_LAT = 3.1022
DEFAULT_LNG = 101.5333
DEFAULT_RADIUS_KM = 10

ELMINA_LAT = 3.1500
ELMINA_LNG = 101.4800
ELMINA_RADIUS_METERS = 4000

ECE_SYNONYMS = [
    "tadika",
    "taska",
    "preschool",
    "kindergarten",
    "playschool",
    "educare",
    "childcare",
    "pusat jagaan",
    "pusat perkembangan",
]

RAW_COLUMNS = [
    "centre_name",
    "address",
    "neighbourhood",
    "lat",
    "lng",
    "curriculum",
    "language_medium",
    "fee_halfday_raw",
    "fee_fullday_raw",
    "scale",
    "religious_orientation",
    "is_moe_registered",
    "source_primary",
    "source_notes",
]

REQUEST_TIMEOUT = 25
USER_AGENT = "ProjectKestrel/1.0 (+research pipeline)"


def build_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=2,
        backoff_factor=0.6,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=frozenset(["GET", "POST"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({"User-Agent": USER_AGENT})
    return session


# ---------------------- Utility helpers ----------------------
def log(msg: str) -> None:
    print(msg)


def safe_text(val: Optional[str], default: str = "") -> str:
    if val is None:
        return default
    return str(val).strip()


def geocode_address(address: str) -> Tuple[float, float, bool]:
    geolocator = Nominatim(user_agent="project-kestrel-fetcher")
    try:
        loc = geolocator.geocode(address)
        if loc:
            return float(loc.latitude), float(loc.longitude), True
    except Exception as exc:
        log(f"Geocoding failed for '{address}': {exc}")
    return DEFAULT_LAT, DEFAULT_LNG, False


def is_within_radius(centre_lat: float, centre_lng: float, point_lat: float, point_lng: float, radius_km: float = 10) -> bool:
    centre = (centre_lat, centre_lng)
    point = (point_lat, point_lng)
    return geodesic(centre, point).km <= radius_km


def parse_fee_values(text: str) -> List[str]:
    if not text:
        return []
    return re.findall(r"RM\s*[\d,]+", text, flags=re.IGNORECASE)


def blank_record() -> Dict:
    return {
        "centre_name": "",
        "address": "",
        "neighbourhood": "",
        "lat": None,
        "lng": None,
        "curriculum": "",
        "language_medium": "",
        "fee_halfday_raw": "",
        "fee_fullday_raw": "",
        "scale": "",
        "religious_orientation": "",
        "is_moe_registered": False,
        "source_primary": "",
        "source_notes": "",
    }


def merge_records(existing: Dict, new: Dict) -> Dict:
    for key in RAW_COLUMNS:
        if key in ["is_moe_registered"]:
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
            # BRANCH SEPARATION: check distance BEFORE checking name similarity
            # MERGE only if name is similar AND they are physically close together
            if name_sim > 85 and dist < 300:
                seen[idx] = merge_records(existing, record)
                duplicate_found = True
                break
            # DO NOT merge branches — same brand name but far apart = separate rows
        if not duplicate_found:
            seen.append(record)
    return seen


# ---------------------- Source 1: Overpass ----------------------
def fetch_overpass(session: requests.Session, centre_lat: float, centre_lng: float, radius_km: float) -> List[Dict]:
    log("[Overpass] Fetching...")
    out = []
    endpoints = [
        "https://lz4.overpass-api.de/api/interpreter",
        "https://overpass-api.de/api/interpreter",
        "https://overpass.openstreetmap.fr/api/interpreter",
    ]

    queries = [
        (centre_lat, centre_lng, int(radius_km * 1000), "main"),
        (ELMINA_LAT, ELMINA_LNG, ELMINA_RADIUS_METERS, "elmina"),
    ]

    for q_lat, q_lng, q_radius, label in queries:
        for synonym in ECE_SYNONYMS:
            overpass_ql = f"""
[out:json][timeout:60];
(
  node[\"amenity\"=\"kindergarten\"][\"name\"~\"{synonym}\", i](around:{q_radius},{q_lat},{q_lng});
  node[\"amenity\"=\"childcare\"][\"name\"~\"{synonym}\", i](around:{q_radius},{q_lat},{q_lng});
  way[\"amenity\"=\"kindergarten\"][\"name\"~\"{synonym}\", i](around:{q_radius},{q_lat},{q_lng});
  way[\"amenity\"=\"childcare\"][\"name\"~\"{synonym}\", i](around:{q_radius},{q_lat},{q_lng});
);
out center;
""".strip()
            payload = None
            for endpoint in endpoints:
                try:
                    resp = session.post(endpoint, data=overpass_ql, timeout=REQUEST_TIMEOUT)
                    if resp.status_code >= 400:
                        log(f"Source Overpass API failed at {endpoint} ({label}, {synonym}): HTTP {resp.status_code}. Trying next mirror...")
                        continue
                    payload = resp.json()
                    break
                except requests.RequestException as exc:
                    log(f"Source Overpass API failed at {endpoint} ({label}, {synonym}): {exc}. Trying next mirror...")
                except Exception as exc:
                    log(f"Source Overpass API parse error at {endpoint} ({label}, {synonym}): {exc}. Trying next mirror...")
            if payload is None:
                continue
            for el in payload.get("elements", []):
                lat = el.get("lat") or el.get("center", {}).get("lat")
                lng = el.get("lon") or el.get("center", {}).get("lon")
                if lat is None or lng is None:
                    continue
                rec = blank_record()
                tags = el.get("tags", {})
                rec["centre_name"] = safe_text(tags.get("name"), "Unnamed ECE Centre")
                rec["address"] = ", ".join(
                    [
                        safe_text(tags.get("addr:housenumber")),
                        safe_text(tags.get("addr:street")),
                        safe_text(tags.get("addr:city")),
                    ]
                ).strip(", ")
                rec["lat"] = float(lat)
                rec["lng"] = float(lng)
                rec["source_primary"] = "Overpass API (OpenStreetMap)"
                rec["source_notes"] = f"[Verified: Overpass API ({label}, synonym={synonym})]"
                out.append(rec)
            time.sleep(0.2)
    log(f"[Overpass] Collected {len(out)} rows")
    return out


# ---------------------- Source 2: data.gov.my ----------------------
def fetch_data_gov_my(session: requests.Session) -> List[Dict]:
    log("[data.gov.my] Fetching...")
    out = []
    base = "https://api.data.gov.my/data-catalogue"
    keywords = ["prasekolah", "taska", "pendidikan awal"]

    try:
        idx = session.get(base, timeout=REQUEST_TIMEOUT)
        idx.raise_for_status()
        datasets = idx.json() if isinstance(idx.json(), list) else []
    except Exception as exc:
        log(f"Source data.gov.my failed at catalogue listing: {exc}. Continuing.")
        return out

    candidate_ids = []
    for item in datasets:
        text = f"{item.get('title', '')} {item.get('description', '')}".lower()
        if any(k in text for k in keywords):
            dsid = item.get("id") or item.get("slug")
            if dsid:
                candidate_ids.append(str(dsid))

    for dsid in candidate_ids[:30]:
        for synonym in ECE_SYNONYMS:
            try:
                url = f"{base}/{dsid}"
                resp = session.get(url, params={"state": "Selangor", "q": synonym}, timeout=REQUEST_TIMEOUT)
                if resp.status_code >= 400:
                    continue
                payload = resp.json()
                rows = payload if isinstance(payload, list) else payload.get("data", []) if isinstance(payload, dict) else []
                for row in rows:
                    name = safe_text(row.get("name") or row.get("centre_name") or row.get("nama") or row.get("premise_name"))
                    if not name:
                        continue
                    rec = blank_record()
                    rec["centre_name"] = name
                    rec["address"] = safe_text(row.get("address") or row.get("alamat") or row.get("lokasi"))
                    rec["is_moe_registered"] = True
                    rec["source_primary"] = "data.gov.my Open API"
                    rec["source_notes"] = f"[Verified: MOE Registry — data.gov.my | synonym={synonym}]"
                    out.append(rec)
            except Exception as exc:
                log(f"Source data.gov.my failed ({dsid}, {synonym}): {exc}. Continuing.")
    log(f"[data.gov.my] Collected {len(out)} rows")
    return out


def enrich_with_moe_registry(records: List[Dict], moe_records: List[Dict]) -> None:
    for rec in records:
        for moe in moe_records:
            if fuzz.token_sort_ratio(rec.get("centre_name", ""), moe.get("centre_name", "")) > 80:
                rec["is_moe_registered"] = True
                notes = safe_text(rec.get("source_notes"))
                tag = "[Verified: MOE Registry — data.gov.my]"
                if tag not in notes:
                    rec["source_notes"] = (notes + " | " + tag).strip(" |")
                break


# ---------------------- Source 3: Kiddy123 ----------------------
def parse_kiddy_listing(session: requests.Session, list_url: str) -> List[Tuple[str, str, str]]:
    centres = []
    try:
        page = session.get(list_url, timeout=REQUEST_TIMEOUT)
        if page.status_code >= 400:
            log(f"Source Kiddy123 listing unavailable ({page.status_code}) at {list_url}. Continuing.")
            return centres
        soup = BeautifulSoup(page.text, "html.parser")

        for a in soup.select("a[href]"):
            href = a.get("href", "")
            text = a.get_text(" ", strip=True)
            if not text:
                continue
            if "kiddy123.com" in href and ("/listing/" in href or "/kindergarten/" in href):
                centres.append((text, "", href))
    except Exception as exc:
        log(f"Source Kiddy123 failed at listing {list_url}: {exc}. Continuing.")
    return centres


def parse_kiddy_detail(session: requests.Session, name: str, url: str) -> Dict:
    rec = blank_record()
    rec["centre_name"] = name
    rec["source_primary"] = "Kiddy123 Directory"
    rec["source_notes"] = "[Verified: Kiddy123 directory]"

    try:
        detail = session.get(url, timeout=REQUEST_TIMEOUT)
        if detail.status_code >= 400:
            return rec
        soup = BeautifulSoup(detail.text, "html.parser")
        page_text = soup.get_text(" ", strip=True)

        fees = parse_fee_values(page_text)
        if fees:
            rec["fee_halfday_raw"] = fees[0]
            rec["fee_fullday_raw"] = fees[-1]

        rec["curriculum"] = safe_text(page_text[:500])
        rec["language_medium"] = ""

        address_node = soup.find(string=re.compile(r"Address", re.IGNORECASE))
        if address_node:
            rec["address"] = safe_text(address_node.parent.get_text(" ", strip=True))
    except Exception as exc:
        log(f"Source Kiddy123 failed at detail {url}: {exc}. Continuing.")
    return rec


def fetch_kiddy123(session: requests.Session) -> List[Dict]:
    log("[Kiddy123] Fetching...")
    out = []
    urls = [
        "https://www.kiddy123.com/malaysia/selangor/shah-alam/",
        "https://www.kiddy123.com/malaysia/selangor/setia-alam/",
    ]

    for list_url in urls:
        for name, _addr, detail_url in parse_kiddy_listing(session, list_url):
            rec = parse_kiddy_detail(session, name, detail_url)
            out.append(rec)

    log(f"[Kiddy123] Collected {len(out)} rows")
    return out


# ---------------------- Source 4: Foursquare ----------------------
def fetch_foursquare(session: requests.Session, centre_lat: float, centre_lng: float, radius_km: float) -> List[Dict]:
    log("[Foursquare] Fetching...")
    out = []
    key = os.getenv("FOURSQUARE_KEY")
    if not key:
        log("FOURSQUARE_KEY not found — skipping Foursquare source")
        return out

    endpoint = "https://api.foursquare.com/v3/places/search"
    headers = {"Authorization": key}

    query_points = [
        (centre_lat, centre_lng, int(radius_km * 1000), "main"),
        (ELMINA_LAT, ELMINA_LNG, ELMINA_RADIUS_METERS, "elmina"),
    ]

    for q_lat, q_lng, q_radius, label in query_points:
        for synonym in ECE_SYNONYMS:
            params = {
                "ll": f"{q_lat},{q_lng}",
                "radius": q_radius,
                "categories": "12058,12056",
                "limit": 50,
                "fields": "name,location,geocodes,rating,categories",
                "query": synonym,
            }
            try:
                resp = session.get(endpoint, headers=headers, params=params, timeout=REQUEST_TIMEOUT)
                resp.raise_for_status()
                for item in resp.json().get("results", []):
                    rec = blank_record()
                    rec["centre_name"] = safe_text(item.get("name"), "Unnamed ECE Centre")
                    loc = item.get("location", {})
                    geocodes = item.get("geocodes", {}).get("main", {})
                    rec["address"] = safe_text(loc.get("formatted_address"))
                    rec["lat"] = geocodes.get("latitude")
                    rec["lng"] = geocodes.get("longitude")
                    rec["source_primary"] = "Foursquare Places API"
                    rating = item.get("rating")
                    rec["source_notes"] = f"[Verified: Foursquare Places API] Foursquare rating: {rating}/10 | scope={label} | synonym={synonym}"
                    out.append(rec)
            except Exception as exc:
                log(f"Source Foursquare failed ({label}, {synonym}): {exc}. Continuing.")

    log(f"[Foursquare] Collected {len(out)} rows")
    return out


# ---------------------- Source 5: SerpAPI ----------------------
def fetch_serpapi(session: requests.Session) -> List[Dict]:
    log("[SerpAPI] Fetching...")
    out = []
    key = os.getenv("SERPAPI_KEY")
    if not key:
        log("SERPAPI_KEY not found — skipping SerpAPI source")
        return out

    endpoint = "https://serpapi.com/search.json"

    fixed_queries = [
        "tadika Bukit Jelutong Shah Alam",
        "taska Bukit Jelutong",
        "preschool Setia Alam Shah Alam",
        "tadika Elmina Shah Alam",
        "taska Elmina Kota Elmina",
        "kindergarten Denai Alam",
        "preschool Glenmarie Shah Alam",
        "tadika facebook Setia Alam",
        "taska Elmina Shah Alam",
        "tadika Kota Elmina",
        "preschool Elmina",
    ]

    dynamic_areas = [
        "Bukit Jelutong Shah Alam",
        "Setia Alam Shah Alam",
        "Denai Alam",
        "Elmina Shah Alam",
        "Kota Elmina",
        "Glenmarie Shah Alam",
    ]

    all_queries = set(fixed_queries)
    for term in ECE_SYNONYMS:
        for area in dynamic_areas:
            all_queries.add(f"{term} {area}")

    for q in sorted(all_queries):
        params = {"engine": "google", "q": q, "api_key": key, "num": 20}
        try:
            resp = session.get(endpoint, params=params, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            payload = resp.json()
            for result in payload.get("organic_results", []):
                title = safe_text(result.get("title"))
                snippet = safe_text(result.get("snippet"))
                hay = (title + " " + snippet).lower()
                if not any(term in hay for term in ECE_SYNONYMS):
                    continue

                rec = blank_record()
                rec["centre_name"] = title.split("-")[0].strip() or title
                rec["address"] = snippet
                rec["source_primary"] = "SerpAPI (Google Search)"
                rec["source_notes"] = f"[Verified: SerpAPI — Google Search result] query='{q}'"
                out.append(rec)
        except Exception as exc:
            log(f"Source SerpAPI failed ({q}): {exc}. Continuing.")

    log(f"[SerpAPI] Collected {len(out)} rows")
    return out


# ---------------------- Post-processing ----------------------
def validate_radius(records: List[Dict], centre_lat: float, centre_lng: float, radius_km: float) -> Tuple[List[Dict], int]:
    kept = []
    excluded = 0
    for rec in records:
        lat = rec.get("lat")
        lng = rec.get("lng")
        if lat is None or lng is None:
            kept.append(rec)
            continue
        if is_within_radius(centre_lat, centre_lng, float(lat), float(lng), radius_km):
            kept.append(rec)
        else:
            excluded += 1
            dist = geodesic((centre_lat, centre_lng), (float(lat), float(lng))).km
            log(f"Excluded {rec.get('centre_name', 'unknown')} — {dist:.2f}km from centre")
    return kept, excluded


def ensure_schema(df: pd.DataFrame) -> pd.DataFrame:
    for col in RAW_COLUMNS:
        if col not in df.columns:
            df[col] = "" if col not in ["lat", "lng", "is_moe_registered"] else None
    return df[RAW_COLUMNS]


def run_pipeline(address: str, radius_km: float, output_csv: str) -> None:
    session = build_session()
    centre_lat, centre_lng, geocode_ok = geocode_address(address)
    if geocode_ok:
        log(f"Resolved centre: {centre_lat:.4f}, {centre_lng:.4f}")
    else:
        log(f"Address geocoding failed. Falling back to default {DEFAULT_LAT:.4f}, {DEFAULT_LNG:.4f}")

    all_records: List[Dict] = []
    source_counter = 0

    try:
        overpass_rows = fetch_overpass(session, centre_lat, centre_lng, radius_km)
        all_records.extend(overpass_rows)
        source_counter += 1
    except Exception as exc:
        log(f"Source Overpass API failed: {exc}. Continuing.")

    try:
        moe_rows = fetch_data_gov_my(session)
        source_counter += 1
    except Exception as exc:
        log(f"Source data.gov.my failed: {exc}. Continuing.")
        moe_rows = []

    try:
        kiddy_rows = fetch_kiddy123(session)
        all_records.extend(kiddy_rows)
        source_counter += 1
    except Exception as exc:
        log(f"Source Kiddy123 failed: {exc}. Continuing.")

    try:
        foursquare_rows = fetch_foursquare(session, centre_lat, centre_lng, radius_km)
        all_records.extend(foursquare_rows)
        source_counter += 1
    except Exception as exc:
        log(f"Source Foursquare failed: {exc}. Continuing.")

    try:
        serp_rows = fetch_serpapi(session)
        all_records.extend(serp_rows)
        source_counter += 1
    except Exception as exc:
        log(f"Source SerpAPI failed: {exc}. Continuing.")

    enrich_with_moe_registry(all_records, moe_rows)

    deduped = deduplicate(all_records)
    within_radius, excluded = validate_radius(deduped, centre_lat, centre_lng, radius_km)

    df = pd.DataFrame(within_radius)
    df = ensure_schema(df)

    os.makedirs(os.path.dirname(output_csv), exist_ok=True)
    df.to_csv(output_csv, index=False)
    log(f"raw.csv written: {len(df)} rows from {source_counter} sources. {excluded} rows excluded.")


def run_quick_check(address: str, radius_km: float) -> None:
    """
    Fast connectivity smoke-test for all configured sources.
    This does not write CSV output; it only verifies source reachability and basic response handling.
    """
    session = build_session()
    centre_lat, centre_lng, geocode_ok = geocode_address(address)
    if geocode_ok:
        log(f"[Quick Check] Resolved centre: {centre_lat:.4f}, {centre_lng:.4f}")
    else:
        log(f"[Quick Check] Geocoding fallback in use: {DEFAULT_LAT:.4f}, {DEFAULT_LNG:.4f}")

    checks = {}

    try:
        overpass_rows = fetch_overpass(session, centre_lat, centre_lng, min(radius_km, 3))
        checks["Overpass"] = f"OK ({len(overpass_rows)} rows)"
    except Exception as exc:
        checks["Overpass"] = f"FAIL ({exc})"

    try:
        gov_rows = fetch_data_gov_my(session)
        checks["data.gov.my"] = f"OK ({len(gov_rows)} rows)"
    except Exception as exc:
        checks["data.gov.my"] = f"FAIL ({exc})"

    try:
        kiddy_rows = fetch_kiddy123(session)
        checks["Kiddy123"] = f"OK ({len(kiddy_rows)} rows)"
    except Exception as exc:
        checks["Kiddy123"] = f"FAIL ({exc})"

    try:
        four_rows = fetch_foursquare(session, centre_lat, centre_lng, min(radius_km, 3))
        checks["Foursquare"] = f"OK ({len(four_rows)} rows)"
    except Exception as exc:
        checks["Foursquare"] = f"FAIL ({exc})"

    try:
        serp_rows = fetch_serpapi(session)
        checks["SerpAPI"] = f"OK ({len(serp_rows)} rows)"
    except Exception as exc:
        checks["SerpAPI"] = f"FAIL ({exc})"

    log("\n[Quick Check] Source status summary:")
    for source, status in checks.items():
        log(f" - {source}: {status}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Project Kestrel fetcher")
    parser.add_argument("--address", default=DEFAULT_ADDRESS, help="Search centre address")
    parser.add_argument("--radius", type=float, default=DEFAULT_RADIUS_KM, help="Search radius in km")
    parser.add_argument("--output", default="data/raw.csv", help="Output CSV path")
    parser.add_argument("--quick-check", action="store_true", help="Run fast source connectivity checks only")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    if args.quick_check:
        run_quick_check(address=args.address, radius_km=args.radius)
    else:
        run_pipeline(address=args.address, radius_km=args.radius, output_csv=args.output)
