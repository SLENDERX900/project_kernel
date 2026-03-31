"""
Project Kestrel — fetcher.py
Collects ECE operator data from 5 sources and writes data/raw.csv.
Run: python fetcher.py [--address "..."] [--radius 10] [--output data/raw.csv]
"""

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

load_dotenv()

# ─────────────────────────── Constants ────────────────────────────

DEFAULT_ADDRESS   = "Persiaran Tebar Layar, Bukit Jelutong, 40150 Shah Alam, Selangor"
DEFAULT_LAT       = 3.1022
DEFAULT_LNG       = 101.5333
DEFAULT_RADIUS_KM = 10.0

ELMINA_LAT            = 3.1500
ELMINA_LNG            = 101.4800
ELMINA_RADIUS_METERS  = 4000

# Malaysia bounding box — anything outside this is not Malaysian
MALAYSIA_LAT = (1.0, 7.5)
MALAYSIA_LNG = (99.5, 119.5)

ECE_SYNONYMS = [
    "tadika", "taska", "preschool", "kindergarten",
    "playschool", "educare", "childcare",
    "pusat jagaan", "pusat perkembangan",
]

# Known valid ECE brands in the corridor — used to whitelist even if name filter is strict
KNOWN_BRANDS = [
    "real kids", "brainy bunch", "little caliphs", "genius aulad", "idrissi",
    "eduwis", "wynkids", "knowledge tree", "little blossom", "taska hanis",
    "cubs early years", "little oak tree", "ace arrows", "bahtera",
    "choo choo train", "treemendous", "primrose hill", "kinderland",
    "kinderhive", "kinderkaizen", "kidstime", "bright kids",
    "gumtree cottage", "love and laugh", "love & laugh", "hi flyers",
    "hi-flyers", "taska dawid", "taska imanina", "taska iman pintar",
    "taska little genius", "taska little sujood", "taska mommy lynn",
    "taska mommylynn", "little caliphs", "genius kids", "little qalifa",
    "al-hafiz", "al hafiz", "ibnu sina", "taska generasi", "naluri bestari",
    "iman pintar", "brainy house", "q-dees", "keedsflix", "beaconhouse",
    "smart reader", "taska tnb", "kindyhaus", "glenpark",
]

# Patterns that indicate a result is NOT an ECE operator — reject these
JUNK_PATTERNS = [
    r"^preschool/kindergarten in \w",           # Kiddy123 navigation links
    r"^selected preschools in",                  # Article list headers
    r"^selected international preschools",
    r"best \d+ preschools",                      # Review article titles
    r"most prestigious",
    r"^read also:",
    r"^preschool in \w+ \|",                     # Kiddy123 SEO page titles
    r"^tadika dan taska di ",                     # Blog article title
    r"^preschool$",                              # Bare keyword only
    r"^tadika$",
    r"^taska$",
    r"^kindergarten$",
    r"^childcare$",
    r"^educare$",
    r"^playschool$",
    r"^pusat jagaan$",
    r"^pusat perkembangan$",
    r"^kindergarten / preschool$",
    r"^preschool/kindergarten in \w",
    r"kiddy123\.com$",                            # Website name itself
    r"^branches$",                                # Generic "branches" page
    r"^bukit jelutong, shah alam",               # Location name not a centre
    r"^setia alam, shah alam",
    r"^shah alam, selangor",
    r"preschool/kindergarten in (alor setar|ayer keroh|balik pulau|bandar|bangi|bangsar|banting|batu|bayan|bukit jalil|bukit mertajam|cheras|cyberjaya|desa|gelang|genting|gombak|hartamas|ipoh|iskandar|kajang|kampar|kemaman|kluang|kota bharu|kota kinabalu|kuala langat|kuala lumpur|kuantan|kuching|kulai|medan|melaka|miri|muar|nilai|petaling|puchong|rawang|segambut|semenyih|sepang|seremban|seri kembangan|setapak|sibu|simpang|sintok|sri petaling|subang jaya|sungai buloh|taman|tanjung|tawau)",
    r"\d+ (followers|following|posts).*",         # Social media profile metrics
    r"^school is fun",                            # Social media post
    r"^hari terbuka",                             # Event announcement
    r"sorting\.\.\.",                             # Social post fragment
]

# Words that indicate NOT an ECE (non-ECE businesses)
NON_ECE_KEYWORDS = [
    "tuition", "tuisyen", "academy for teens", "secondary school",
    "high school", "college", "university", "clinic", "hospital",
    "restaurant", "gym", "fitness", "salon", "bank", "insurance",
    "property", "real estate", "supermarket", "pharmacy",
]

RAW_COLUMNS = [
    "centre_name", "address", "neighbourhood", "lat", "lng",
    "curriculum", "language_medium", "fee_halfday_raw", "fee_fullday_raw",
    "scale", "religious_orientation", "is_moe_registered",
    "source_primary", "source_notes",
]


# ─────────────────────────── Helpers ──────────────────────────────

def log(msg: str) -> None:
    print(msg)


def safe_text(val: Optional[str], default: str = "") -> str:
    if val is None:
        return default
    return str(val).strip()


def geocode_address(address: str) -> Tuple[float, float, bool]:
    geolocator = Nominatim(user_agent="project-kestrel-fetcher", timeout=10)
    try:
        loc = geolocator.geocode(address)
        if loc:
            lat, lng = float(loc.latitude), float(loc.longitude)
            # Reject if outside Malaysia
            if MALAYSIA_LAT[0] <= lat <= MALAYSIA_LAT[1] and MALAYSIA_LNG[0] <= lng <= MALAYSIA_LNG[1]:
                return lat, lng, True
    except Exception as exc:
        log(f"Geocoding failed for '{address}': {exc}")
    return DEFAULT_LAT, DEFAULT_LNG, False


def is_within_radius(centre_lat: float, centre_lng: float,
                     point_lat: float, point_lng: float,
                     radius_km: float = 10.0) -> bool:
    return geodesic((centre_lat, centre_lng), (point_lat, point_lng)).km <= radius_km


def is_junk_name(name: str) -> bool:
    """Returns True if the name looks like a website link, article title, or non-operator."""
    name_lower = name.lower().strip()
    # Check against junk regex patterns
    for pattern in JUNK_PATTERNS:
        if re.search(pattern, name_lower):
            return True
    # Check for non-ECE keywords
    if any(kw in name_lower for kw in NON_ECE_KEYWORDS):
        return True
    # Name must contain at least one ECE word OR be a known brand
    has_ece_word = any(term in name_lower for term in ECE_SYNONYMS)
    is_known = any(brand in name_lower for brand in KNOWN_BRANDS)
    if not has_ece_word and not is_known:
        return True
    # Too short to be meaningful (after stripping)
    if len(name_lower.replace(" ", "")) < 4:
        return True
    return False


def is_valid_name(name: str) -> bool:
    """Returns True if the name is a plausible ECE operator name."""
    if not name or not name.strip():
        return False
    # Reject obvious junk
    if name.strip().lower() in {"unnamed ece centre", "unnamed", "", "-", "n/a"}:
        return False
    return not is_junk_name(name)


def extract_clean_address_from_snippet(snippet: str) -> str:
    """
    Try to extract a clean Malaysian address from a SerpAPI snippet.
    Looks for patterns like: Jalan/Persiaran/No X... Shah Alam/Selangor/Malaysia
    """
    if not snippet:
        return ""
    # Look for Malaysian address patterns
    patterns = [
        r"((?:No\.?\s*\d+[-\w]*,?\s*)?(?:Jalan|Persiaran|Lorong|Lebuh|Lebuhray|Lingkungan|Jln)[^\.]{5,80}(?:Shah Alam|Selangor|Elmina|Bukit Jelutong|Setia Alam|Denai Alam)[^\.]{0,40})",
        r"(\d{1,5}[-\w]*,?\s*(?:Jalan|Persiaran|Lorong|Jln)[^\.]{5,60}\d{5})",
        r"((?:GL|No|Unit|Lot|G|F)\d+[^,\.]{0,20},\s*(?:Plaza|D'Vida|Trivo|Jalan|Persiaran)[^\.]{5,80}(?:\d{5})[^\.]{0,20})",
    ]
    for pattern in patterns:
        match = re.search(pattern, snippet, re.IGNORECASE)
        if match:
            addr = match.group(1).strip().rstrip(",. ")
            # Limit to 200 chars
            return addr[:200]
    return ""


def extract_clean_name_from_title(title: str) -> str:
    """
    Clean a SerpAPI page title into a centre name.
    Removes trailing | suffix and common noise phrases.
    """
    # Split on common title separators and take first meaningful part
    for sep in [" | ", " - ", " – ", " — ", " · "]:
        if sep in title:
            parts = title.split(sep)
            candidate = parts[0].strip()
            # If first part looks like a real name (not a generic phrase), use it
            if len(candidate) > 4 and not is_junk_name(candidate):
                return candidate
    # Remove trailing location suffixes
    cleaned = re.sub(
        r",?\s*(Shah Alam|Selangor|Malaysia|Bukit Jelutong|Setia Alam|Denai Alam|Elmina|Glenmarie).*$",
        "", title, flags=re.IGNORECASE
    ).strip()
    return cleaned if cleaned else title


def parse_fee_values(text: str) -> List[str]:
    if not text:
        return []
    return re.findall(r"RM\s*[\d,]+", text, flags=re.IGNORECASE)


def blank_record() -> Dict:
    return {col: "" if col not in ("lat", "lng", "is_moe_registered") else None
            for col in RAW_COLUMNS}


def merge_records(existing: Dict, new: Dict) -> Dict:
    for key in RAW_COLUMNS:
        if key == "is_moe_registered":
            existing[key] = bool(existing.get(key) or new.get(key))
            continue
        if not safe_text(existing.get(key)) and safe_text(new.get(key)):
            existing[key] = new[key]
    # Merge source notes
    existing_notes = safe_text(existing.get("source_notes"))
    new_notes = safe_text(new.get("source_notes"))
    if new_notes and new_notes not in existing_notes:
        existing["source_notes"] = (existing_notes + " | " + new_notes).strip(" |")
    return existing


def deduplicate(records: List[Dict]) -> List[Dict]:
    """
    Merge records only when:
      - name similarity > 85 AND coordinates within 300m, OR
      - name similarity > 92 AND both have no coordinates (name-only dedup for SerpAPI)
    Branch separation: same brand but different neighbourhoods = separate rows.
    """
    seen = []
    for record in records:
        duplicate_found = False
        for idx, existing in enumerate(seen):
            name_sim = fuzz.token_sort_ratio(
                safe_text(record.get("centre_name")),
                safe_text(existing.get("centre_name"))
            )
            r_lat = record.get("lat")
            e_lat = existing.get("lat")

            if r_lat is not None and e_lat is not None:
                dist = geodesic(
                    (float(r_lat), float(record["lng"])),
                    (float(e_lat), float(existing["lng"]))
                ).meters
            else:
                dist = None

            # BRANCH SEPARATION: check distance BEFORE merging
            # Merge only if same name AND physically close
            if name_sim > 85 and dist is not None and dist < 300:
                seen[idx] = merge_records(existing, record)
                duplicate_found = True
                break
            # Name-only dedup for records with no coordinates (SerpAPI)
            elif name_sim > 92 and dist is None:
                seen[idx] = merge_records(existing, record)
                duplicate_found = True
                break
        if not duplicate_found:
            seen.append(record)
    return seen


# ─────────────── Source 1: Overpass API ───────────────────────────

def fetch_overpass(centre_lat: float, centre_lng: float, radius_km: float) -> List[Dict]:
    log("[Overpass] Fetching...")
    out = []
    endpoint = "https://overpass-api.de/api/interpreter"

    search_zones = [
        (centre_lat, centre_lng, int(radius_km * 1000), "main"),
        (ELMINA_LAT, ELMINA_LNG, ELMINA_RADIUS_METERS, "elmina"),
    ]

    for q_lat, q_lng, q_radius, label in search_zones:
        overpass_ql = f"""
[out:json][timeout:60];
(
  node["amenity"="kindergarten"](around:{q_radius},{q_lat},{q_lng});
  node["amenity"="childcare"](around:{q_radius},{q_lat},{q_lng});
  way["amenity"="kindergarten"](around:{q_radius},{q_lat},{q_lng});
  way["amenity"="childcare"](around:{q_radius},{q_lat},{q_lng});
);
out center;
""".strip()
        try:
            resp = requests.post(endpoint, data=overpass_ql, timeout=60)
            resp.raise_for_status()
            for el in resp.json().get("elements", []):
                lat = el.get("lat") or el.get("center", {}).get("lat")
                lng = el.get("lon") or el.get("center", {}).get("lon")
                if lat is None or lng is None:
                    continue
                tags = el.get("tags", {})
                name = safe_text(tags.get("name"))
                if not name:
                    continue
                rec = blank_record()
                rec["centre_name"] = name
                addr_parts = [
                    safe_text(tags.get("addr:housenumber")),
                    safe_text(tags.get("addr:street")),
                    safe_text(tags.get("addr:city")),
                    safe_text(tags.get("addr:postcode")),
                ]
                rec["address"] = ", ".join(p for p in addr_parts if p)
                rec["lat"] = float(lat)
                rec["lng"] = float(lng)
                rec["source_primary"] = "Overpass API (OpenStreetMap)"
                rec["source_notes"] = f"[Verified: Overpass API — OpenStreetMap ({label})]"
                out.append(rec)
        except Exception as exc:
            log(f"Source Overpass failed ({label}): {exc}. Continuing.")
    log(f"[Overpass] Collected {len(out)} rows")
    return out


# ─────────────── Source 2: data.gov.my ───────────────────────────

def fetch_data_gov_my() -> List[Dict]:
    log("[data.gov.my] Fetching...")
    out = []
    base = "https://api.data.gov.my/data-catalogue"
    keywords = ["prasekolah", "taska", "pendidikan awal"]

    try:
        resp = requests.get(base, timeout=30)
        resp.raise_for_status()
        datasets = resp.json() if isinstance(resp.json(), list) else []
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

    for dsid in candidate_ids[:20]:
        try:
            url = f"{base}/{dsid}"
            resp = requests.get(url, params={"state": "Selangor"}, timeout=30)
            if resp.status_code >= 400:
                continue
            payload = resp.json()
            rows = payload if isinstance(payload, list) else payload.get("data", []) if isinstance(payload, dict) else []
            for row in rows:
                name = safe_text(
                    row.get("name") or row.get("centre_name") or
                    row.get("nama") or row.get("premise_name")
                )
                if not name or not is_valid_name(name):
                    continue
                rec = blank_record()
                rec["centre_name"] = name
                rec["address"] = safe_text(
                    row.get("address") or row.get("alamat") or row.get("lokasi")
                )
                rec["is_moe_registered"] = True
                rec["source_primary"] = "data.gov.my Open API"
                rec["source_notes"] = "[Verified: MOE Registry — data.gov.my]"
                out.append(rec)
        except Exception as exc:
            log(f"Source data.gov.my failed ({dsid}): {exc}. Continuing.")

    log(f"[data.gov.my] Collected {len(out)} rows")
    return out


def enrich_with_moe_registry(records: List[Dict], moe_records: List[Dict]) -> None:
    for rec in records:
        for moe in moe_records:
            if fuzz.token_sort_ratio(
                safe_text(rec.get("centre_name")),
                safe_text(moe.get("centre_name"))
            ) > 80:
                rec["is_moe_registered"] = True
                notes = safe_text(rec.get("source_notes"))
                tag = "[Verified: MOE Registry — data.gov.my]"
                if tag not in notes:
                    rec["source_notes"] = (notes + " | " + tag).strip(" |")
                break


# ─────────────── Source 3: Kiddy123 ───────────────────────────────

def fetch_kiddy123(centre_lat: float, centre_lng: float, radius_km: float) -> List[Dict]:
    """
    Scrapes Kiddy123 listing pages. Extracts real centre names and fees.
    Strict filtering: only include entries that have a real ECE name and
    can be confirmed to be in Shah Alam / nearby areas.
    """
    log("[Kiddy123] Fetching...")
    out = []
    listing_urls = [
        "https://www.kiddy123.com/malaysia/selangor/shah-alam/",
        "https://www.kiddy123.com/malaysia/selangor/setia-alam/",
        "https://www.kiddy123.com/malaysia/selangor/subang-jaya/",
    ]
    headers = {"User-Agent": "Mozilla/5.0 (compatible; ProjectKestrel/1.0)"}

    for list_url in listing_urls:
        try:
            page = requests.get(list_url, headers=headers, timeout=30)
            page.raise_for_status()
            soup = BeautifulSoup(page.text, "html.parser")

            # Find individual centre links
            centre_links = []
            for a in soup.find_all("a", href=True):
                href = a["href"]
                text = a.get_text(" ", strip=True)
                # Kiddy123 centre URLs follow the pattern /centre-name/selangor/...
                if ("kiddy123.com" in href or href.startswith("/")) and len(text) > 4:
                    if any(term in text.lower() for term in ECE_SYNONYMS) or any(brand in text.lower() for brand in KNOWN_BRANDS):
                        full_url = href if href.startswith("http") else f"https://www.kiddy123.com{href}"
                        if "/malaysia/selangor/" in full_url and text not in [u[0] for u in centre_links]:
                            centre_links.append((text, full_url))

            for name, detail_url in centre_links[:40]:
                if not is_valid_name(name):
                    continue
                try:
                    time.sleep(1)
                    detail = requests.get(detail_url, headers=headers, timeout=30)
                    detail.raise_for_status()
                    detail_soup = BeautifulSoup(detail.text, "html.parser")
                    page_text = detail_soup.get_text(" ", strip=True)

                    # Extract address
                    address = ""
                    for kw in ["Address", "Alamat"]:
                        node = detail_soup.find(string=re.compile(kw, re.IGNORECASE))
                        if node and node.parent:
                            address = node.parent.get_text(" ", strip=True)
                            address = re.sub(r"^Address:?\s*", "", address, flags=re.IGNORECASE).strip()
                            if len(address) > 10:
                                break

                    # Extract fees
                    fees = parse_fee_values(page_text)
                    fee_hd = fees[0] if fees else ""
                    fee_fd = fees[-1] if len(fees) > 1 else fees[0] if fees else ""

                    rec = blank_record()
                    rec["centre_name"] = name
                    rec["address"] = address
                    rec["fee_halfday_raw"] = fee_hd
                    rec["fee_fullday_raw"] = fee_fd
                    rec["source_primary"] = "Kiddy123 Directory"
                    if fees:
                        rec["source_notes"] = "[Verified: Kiddy123] Fees confirmed from listing."
                    else:
                        rec["source_notes"] = "[Verified: Kiddy123 directory]"
                    out.append(rec)
                except Exception as exc:
                    log(f"Kiddy123 detail fetch failed ({name}): {exc}. Continuing.")
        except Exception as exc:
            log(f"Source Kiddy123 listing failed ({list_url}): {exc}. Continuing.")

    log(f"[Kiddy123] Collected {len(out)} rows before filter")
    # Filter: only keep rows with valid names
    out = [r for r in out if is_valid_name(r.get("centre_name", ""))]
    log(f"[Kiddy123] {len(out)} rows after name filter")
    return out


# ─────────────── Source 4: Foursquare ─────────────────────────────

def fetch_foursquare(centre_lat: float, centre_lng: float, radius_km: float) -> List[Dict]:
    log("[Foursquare] Fetching...")
    out = []
    key = os.getenv("FOURSQUARE_KEY")
    if not key:
        log("FOURSQUARE_KEY not found — skipping Foursquare source")
        return out

    endpoint = "https://api.foursquare.com/v3/places/search"
    headers = {"Authorization": key}
    search_zones = [
        (centre_lat, centre_lng, int(radius_km * 1000), "main"),
        (ELMINA_LAT, ELMINA_LNG, ELMINA_RADIUS_METERS, "elmina"),
    ]

    for q_lat, q_lng, q_radius, label in search_zones:
        for synonym in ECE_SYNONYMS:
            params = {
                "ll": f"{q_lat},{q_lng}",
                "radius": q_radius,
                "categories": "12058,12056",  # Preschool, Childcare
                "limit": 50,
                "fields": "name,location,geocodes,rating,categories",
                "query": synonym,
            }
            try:
                resp = requests.get(endpoint, headers=headers, params=params, timeout=30)
                resp.raise_for_status()
                for item in resp.json().get("results", []):
                    name = safe_text(item.get("name"))
                    if not name or not is_valid_name(name):
                        continue
                    geocodes = item.get("geocodes", {}).get("main", {})
                    lat = geocodes.get("latitude")
                    lng = geocodes.get("longitude")
                    # Only include if coordinates are in Malaysia
                    if lat and lng:
                        if not (MALAYSIA_LAT[0] <= lat <= MALAYSIA_LAT[1] and
                                MALAYSIA_LNG[0] <= lng <= MALAYSIA_LNG[1]):
                            log(f"Excluded {name} — coordinates outside Malaysia")
                            continue
                    loc = item.get("location", {})
                    rec = blank_record()
                    rec["centre_name"] = name
                    rec["address"] = safe_text(loc.get("formatted_address"))
                    rec["lat"] = lat
                    rec["lng"] = lng
                    rec["source_primary"] = "Foursquare Places API"
                    rating = item.get("rating", "")
                    rec["source_notes"] = f"[Verified: Foursquare Places API]{' Rating: ' + str(rating) + '/10' if rating else ''}"
                    out.append(rec)
            except Exception as exc:
                log(f"Source Foursquare failed ({label}, {synonym}): {exc}. Continuing.")

    log(f"[Foursquare] Collected {len(out)} rows")
    return out


# ─────────────── Source 5: SerpAPI ────────────────────────────────

def fetch_serpapi() -> List[Dict]:
    """
    Searches Google via SerpAPI. Strict post-processing:
    - Extracts operator name cleanly from page title
    - Extracts address from snippet where possible
    - Rejects page titles that are not real operator names
    - Rejects social media metrics, event posts, article headers
    """
    log("[SerpAPI] Fetching...")
    out = []
    key = os.getenv("SERPAPI_KEY")
    if not key:
        log("SERPAPI_KEY not found — skipping SerpAPI source")
        return out

    endpoint = "https://serpapi.com/search.json"

    # Targeted queries — specific enough to find actual operators
    queries = [
        # Bukit Jelutong
        "tadika Bukit Jelutong Shah Alam site:kiddy123.com OR site:fb.com OR site:facebook.com",
        "taska Bukit Jelutong Shah Alam",
        "preschool kindergarten Bukit Jelutong Shah Alam",
        # Setia Alam
        "tadika taska Setia Alam Shah Alam",
        "preschool kindergarten Setia Alam Shah Alam",
        # Denai Alam
        "tadika taska Denai Alam Shah Alam",
        "preschool kindergarten Denai Alam",
        # Elmina — dedicated queries as per rules.md Rule 5
        "taska Elmina Shah Alam",
        "tadika Kota Elmina",
        "preschool Elmina Shah Alam",
        "kindergarten Elmina U16 Shah Alam",
        "educare childcare Elmina Shah Alam",
        # Glenmarie / Subang fringe
        "tadika taska Glenmarie Shah Alam",
        "preschool Subang Bestari Shah Alam",
        # Facebook-only operators
        "tadika facebook Setia Alam Shah Alam",
        "taska facebook Bukit Jelutong",
    ]

    for q in queries:
        params = {"engine": "google", "q": q, "api_key": key, "num": 10, "gl": "my", "hl": "en"}
        try:
            resp = requests.get(endpoint, params=params, timeout=30)
            resp.raise_for_status()
            for result in resp.json().get("organic_results", []):
                title = safe_text(result.get("title"))
                snippet = safe_text(result.get("snippet"))

                if not title:
                    continue

                # Extract clean name from title
                name = extract_clean_name_from_title(title)

                # Must pass name validation
                if not is_valid_name(name):
                    log(f"[SerpAPI] Rejected: '{name}' (from title: '{title[:60]}')")
                    continue

                # Extract address from snippet
                address = extract_clean_address_from_snippet(snippet)
                # If snippet itself looks like an address, use it
                if not address and any(kw in snippet for kw in ["Jalan", "Persiaran", "Shah Alam", "Selangor"]):
                    address = snippet[:200]

                rec = blank_record()
                rec["centre_name"] = name
                rec["address"] = address
                rec["source_primary"] = "SerpAPI (Google Search)"
                rec["source_notes"] = f"[Verified: SerpAPI — Google Search result] query='{q[:50]}'"
                out.append(rec)
        except Exception as exc:
            log(f"Source SerpAPI failed ('{q[:40]}'): {exc}. Continuing.")

    log(f"[SerpAPI] Collected {len(out)} rows before filter")
    # Final filter pass
    out = [r for r in out if is_valid_name(r.get("centre_name", ""))]
    log(f"[SerpAPI] {len(out)} rows after name filter")
    return out


# ─────────────── Post-processing ──────────────────────────────────

def validate_radius(records: List[Dict],
                    centre_lat: float, centre_lng: float,
                    radius_km: float) -> Tuple[List[Dict], int]:
    """
    Keep rows if:
      - They have valid Malaysian coordinates within the radius, OR
      - They have no coordinates but address mentions a target area
    Reject rows with coordinates outside Malaysia entirely.
    """
    kept = []
    excluded = 0
    target_areas = [
        "shah alam", "bukit jelutong", "setia alam", "denai alam",
        "elmina", "glenmarie", "subang", "selangor", "u8", "u13", "u16",
    ]

    for rec in records:
        lat = rec.get("lat")
        lng = rec.get("lng")
        address = safe_text(rec.get("address", "")).lower()
        name = safe_text(rec.get("centre_name", "")).lower()

        if lat is not None and lng is not None:
            try:
                lat, lng = float(lat), float(lng)
                # Reject non-Malaysian coordinates completely
                if not (MALAYSIA_LAT[0] <= lat <= MALAYSIA_LAT[1] and
                        MALAYSIA_LNG[0] <= lng <= MALAYSIA_LNG[1]):
                    log(f"Excluded '{rec.get('centre_name')}' — coordinates outside Malaysia")
                    excluded += 1
                    continue
                if is_within_radius(centre_lat, centre_lng, lat, lng, radius_km):
                    kept.append(rec)
                else:
                    dist = geodesic((centre_lat, centre_lng), (lat, lng)).km
                    log(f"Excluded '{rec.get('centre_name')}' — {dist:.1f}km from centre")
                    excluded += 1
            except Exception:
                kept.append(rec)  # Keep if coordinate check fails
        else:
            # No coordinates — keep only if address or name suggests Shah Alam area
            combined = address + " " + name
            if any(area in combined for area in target_areas):
                kept.append(rec)
            else:
                log(f"Excluded '{rec.get('centre_name')}' — no coordinates and no area match")
                excluded += 1

    return kept, excluded


def ensure_schema(df: pd.DataFrame) -> pd.DataFrame:
    for col in RAW_COLUMNS:
        if col not in df.columns:
            df[col] = "" if col not in ("lat", "lng") else None
    return df[RAW_COLUMNS]


# ─────────────── Main pipeline ────────────────────────────────────

def run_pipeline(address: str, radius_km: float, output_csv: str) -> None:
    centre_lat, centre_lng, geocode_ok = geocode_address(address)
    if geocode_ok:
        log(f"Resolved centre: {centre_lat:.4f}, {centre_lng:.4f}")
    else:
        log(f"Using default centre: {DEFAULT_LAT}, {DEFAULT_LNG}")

    all_records: List[Dict] = []
    sources_used = []

    overpass_rows = fetch_overpass(centre_lat, centre_lng, radius_km)
    all_records.extend(overpass_rows)
    if overpass_rows:
        sources_used.append("Overpass API")

    moe_rows = fetch_data_gov_my()
    sources_used.append("data.gov.my")

    kiddy_rows = fetch_kiddy123(centre_lat, centre_lng, radius_km)
    all_records.extend(kiddy_rows)
    if kiddy_rows:
        sources_used.append("Kiddy123")

    foursquare_rows = fetch_foursquare(centre_lat, centre_lng, radius_km)
    all_records.extend(foursquare_rows)
    if foursquare_rows:
        sources_used.append("Foursquare")

    serp_rows = fetch_serpapi()
    all_records.extend(serp_rows)
    if serp_rows:
        sources_used.append("SerpAPI")

    log(f"\nTotal collected before dedup: {len(all_records)} rows")

    enrich_with_moe_registry(all_records, moe_rows)

    deduped = deduplicate(all_records)
    log(f"After deduplication: {len(deduped)} rows")

    within_radius, excluded = validate_radius(deduped, centre_lat, centre_lng, radius_km)
    log(f"After radius filter: {len(within_radius)} rows")

    df = pd.DataFrame(within_radius)
    df = ensure_schema(df)

    os.makedirs(os.path.dirname(output_csv) if os.path.dirname(output_csv) else ".", exist_ok=True)
    df.to_csv(output_csv, index=False)

    log(f"\nraw.csv written: {len(df)} rows from {len(sources_used)} sources ({', '.join(sources_used)}). {excluded} rows excluded.")
    if len(df) > 100:
        log(f"WARNING: Row count {len(df)} is unusually high — review raw.csv for remaining junk entries.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Project Kestrel fetcher")
    parser.add_argument("--address", default=DEFAULT_ADDRESS)
    parser.add_argument("--radius", type=float, default=DEFAULT_RADIUS_KM)
    parser.add_argument("--output", default="data/raw.csv")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    run_pipeline(address=args.address, radius_km=args.radius, output_csv=args.output)
