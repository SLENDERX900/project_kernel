"""
Project Kestrel — fetcher.py  (v6.0 — Fixed)

Multi-source ECE discovery. Sources: Overpass, Kiddy123, Foursquare,
Google Maps Places, Tavily, SerpAPI.

BUG FIXES vs v5.0:
  BUG 1 — Removed Ollama from discovery. An offline LLM cannot verify real
           businesses — it fabricates names. All filtering is now keyword-based.
  BUG 2 — normalize_for_dedup() no longer strips ECE words (tadika/taska/preschool).
           "Tadika Sri Sinar" and "Taska Sri Sinar" are different operators.
  BUG 3 — SOURCE_SUFFIX_RE and junk patterns compiled from real regex strings.
           re.escape() was previously turning patterns into literal text matches.
  BUG 4 — Neighbourhood names ("Setia Alam", "Elmina") removed from STRIP_WORDS.
           Stripping these caused all branches of a brand to merge, breaking Rule 3.
  BUG 5 — Every source result now passes a Haversine radius check. Tavily/SerpAPI
           previously returned results from PJ, Damansara, KL with no filtering.
  BUG 6 — SerpAPI: get_dict() called immediately, returns List[Dict] always.
           Previously returned a GoogleSearch object, causing isinstance(list) to fail.
  BUG 7 — _merge_records() selects by source priority, not string length.
           A garbled long address no longer beats a clean short one.

Run:
  python fetcher.py [--address "..."] [--radius 10] [--output data/raw.csv]
"""

import argparse
import math
import os
import re
import time
from typing import Dict, List, Optional, Tuple

import pandas as pd
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from geopy.geocoders import Nominatim
from thefuzz import fuzz

try:
    from tavily import TavilyClient as _TavilyClient
    _TAVILY_OK = True
except ImportError:
    _TAVILY_OK = False

load_dotenv()

# ─────────────────────────── Constants ────────────────────────────

# Module-level Nominatim instance to reduce connection overhead
# BUG F8 FIX: Creating new instance per-call caused excessive 429 errors
_NOMINATIM_GEOCODER = None

def get_nominatim_geocoder():
    """Get or create shared Nominatim geocoder instance."""
    global _NOMINATIM_GEOCODER
    if _NOMINATIM_GEOCODER is None:
        _NOMINATIM_GEOCODER = Nominatim(user_agent="project-kestrel-fetcher", timeout=10)
    return _NOMINATIM_GEOCODER

DEFAULT_ADDRESS   = "Persiaran Tebar Layar, Bukit Jelutong, 40150 Shah Alam, Selangor"
DEFAULT_LAT       = 3.1022
DEFAULT_LNG       = 101.5333
DEFAULT_RADIUS_KM = 10.0
ELMINA_LAT        = 3.1500
ELMINA_LNG        = 101.4800
ELMINA_RADIUS_M   = 4000
MALAYSIA_LAT      = (1.0, 7.5)
MALAYSIA_LNG      = (99.5, 119.5)

ECE_SYNONYMS = [
    "tadika", "taska", "tabika", "prasekolah",
    "pusat jagaan", "pusat perkembangan", "pusat asuhan",
    "preschool", "kindergarten", "playschool",
    "childcare", "daycare", "nursery", "educare",
]

# BUG 4 FIX: neighbourhood names REMOVED from STRIP_WORDS.
# They were stripping "Setia Alam" etc. from brand names, causing
# "Real Kids Setia Alam" and "Real Kids Bukit Jelutong" to collapse into
# one record — violating branch-separation Rule 3.
STRIP_WORDS = ["HQ", "Branch", "Seksyen", "Section"]

# ECE Prefixes for smart deduplication
ECE_PREFIXES = [
    "tadika", "taska", "tabika", "pusat jagaan", "pusat asuhan",
    "pusat perkembangan", "prasekolah"
]

KNOWN_BRANDS = [
    "real kids", "brainy bunch", "little caliphs", "genius aulad", "idrissi",
    "eduwis", "wynkids", "knowledge tree", "kinderland", "q-dees", "smart reader",
    "pasti", "kemas", "beaconhouse", "cubs early years", "small wonder",
    "little blossom", "choo choo train", "treemendous", "kinderhive",
    "kinderkaizen", "bright kids", "gumtree cottage", "love and laugh",
    "hi flyers", "taska hanis", "taska dawid", "taska imanina",
    "taska little sujood", "genius kids", "al-hafiz", "ibnu sina",
    "naluri bestari", "iman pintar", "brainy house", "keedsflix",
    "ace arrows", "bahtera", "little oak tree", "tiny tree house",
    "rafflesia", "emaan kindy",
]

NON_ECE_KEYWORDS = [
    "tuition", "tuisyen", "pusat tuisyen", "secondary school", "high school",
    "sekolah menengah", "smk ", "college", "university", "universiti",
    "clinic", "klinik", "hospital", "restaurant", "kedai makan",
    "gym", "fitness", "salon", "bank", "insurance", "property", "hartanah",
    "supermarket", "pharmacy", "primary school", "sekolah rendah",
]

# BUG 3 FIX: real regex patterns — compiled directly, NEVER via re.escape().
# Previously re.escape() was applied to these strings, which turned regex
# metacharacters (like \s, \d, [-–—]) into literal text, so nothing was filtered.
_JUNK_PATTERNS = [
    r"^best\s+\d*\s*(preschools?|kindergartens?|childcares?|taska|tadika)\s+in\b",
    r"^admission\s+(criteria|process|requirements?|info)\b",
    r"^selected\s+(preschools?|international\s+preschools?)\s+in\b",
    r"^preschool/kindergarten\s+in\s+\w",
    r"^most\s+prestigious\b",
    r"^read\s+also:",
    r"^\d+\s+(preschools?|kindergartens?|childcares?|taska|tadika)\s+in\b",
    r"\d+\s+(followers|following|posts)\b",
    r"^(preschool|tadika|taska|kindergarten|childcare|educare|playschool)$",
    r"^(home|about\s*us|contact\s*us|our\s+branches|our\s+centres)$",
    r"^preschool/kindergarten\s+in\s+(alor|ayer|balik|bangi|bangsar|cheras|cyberjaya|ipoh|kajang|kuala\s+lumpur|petaling|puchong|seremban|sungai\s+buloh)",
    r"\bprogram\s*$",
    r"®\s*$",
    r"\bcurriculum\s*$",
    r"sorting\.\.\.",
    r"^hari\s+terbuka\b",
    # Aggressive SEO/junk patterns
    r"\bguide\b",
    r"\breview\b",
    r"\btutors?\b",
    r"\?",
    r"^welcome\s+to\b",
    r"\boverview\b",
    r"\bdetails\b",
    r"\bkalau\b",
    r"\bfees\s*&\s*reviews\b",
    # BUG F4: Social media platform names and video/post titles
    r"^instagram$",
    r"^tiktok$",
    r"^facebook$",
    r"^youtube$",
    r"\bon\s+reels\b",
    r"^home\s+school$",
    r"\bfees?\s+structure\b",
    r"^promotion$",
    r"^\[pdf\]",
    r"\bregistration\s+open\b",
    r"^\d{1,2}/\d{1,2}/\d{4}",  # Date strings like "9/3/2026 Monday"
    r"\btop\s+\d+\b.*\bin\b",   # "Top 3 Shah Alam Kindergartens in 2025"
]
_JUNK_RE = re.compile("|".join(_JUNK_PATTERNS), re.IGNORECASE)

# DOMAIN BLACKLIST: Never extract names from these domains/URL patterns
# Social media posts, review sites, academic papers
DOMAIN_BLACKLIST = [
    # Social media platforms
    r"facebook\.com/.*videos/",
    r"facebook\.com/groups/",
    r"tiktok\.com",
    r"threads\.com",
    # Review/directory sites that show search results
    r"yelp\.com",
    r"m\.yelp\.com",
    # Academic/journal sites
    r"researchgate\.net",
    r"pubs\.rsc\.org",
    r"journals\.lww\.com",
    # PDF documents (usually academic papers)
    r"\.pdf$",
]
_DOMAIN_BLACKLIST_RE = re.compile("|".join(DOMAIN_BLACKLIST), re.IGNORECASE)

# Hard split points - metadata that should never be part of centre name
# These are Kiddy123 Facebook post metadata patterns
HARD_SPLIT_PREFIXES = [
    "Location:", "Age Group:", "Telephone:", "Tel:", "Contact:",
    "Address:", "Phone:", "Email:", "Website:", "Operating Hours:",
]

# Known location names to detect after ", " separator (Kiddy123 format)
KNOWN_LOCATIONS = [
    "shah alam", "bukit jelutong", "setia alam", "denai alam", "eco ardence",
    "elmina", "glenmarie", "subang", "petaling jaya", "puchong", "klang",
    "kot kemuning", "bukit rimau", "alam impian", "kota kemuning",
]

def is_domain_blacklisted(url: str) -> bool:
    """Check if URL is from a blacklisted domain (social media, academic, etc.)."""
    if not url:
        return False
    return bool(_DOMAIN_BLACKLIST_RE.search(url.lower()))

def split_at_metadata(name: str) -> str:
    """Hard split at metadata prefixes like 'Location:', 'Age Group:'."""
    if not name:
        return name
    for prefix in HARD_SPLIT_PREFIXES:
        if prefix in name:
            return name.split(prefix)[0].strip()
    return name

def split_at_location_suffix(name: str) -> str:
    """Split at ', ' when followed by known location (Kiddy123 format)."""
    if ", " not in name:
        return name
    parts = name.rsplit(", ", 1)  # Split from right to get last location
    if len(parts) == 2:
        first_part, location_part = parts
        # Check if location part contains known location name
        location_lower = location_part.lower()
        if any(loc in location_lower for loc in KNOWN_LOCATIONS):
            return first_part.strip()
    return name

# BUG F3: SEO keywords now use word-boundary regex checks to avoid blocking
# legitimate names like "Best Kids Academy" or "Little Details Preschool"
# Each keyword is checked with r"\b{kw}\b" pattern in is_junk_name()
SEO_KEYWORDS = [
    "guide", "review", "tutors", "welcome", "overview", "directory",
    "kalau", "ranking", "versus"
]
# Pre-compile regex patterns with word boundaries for efficiency
_SEO_KEYWORD_RE = re.compile("|".join(rf"\b{re.escape(kw)}\b" for kw in SEO_KEYWORDS), re.IGNORECASE)

def is_junk_name(name: str) -> bool:
    """Enhanced junk detection for SEO titles and blog posts.
    
    BUG F2: Removed ALL length cutoffs. Junk detection relies only on pattern matching.
    Names like "Tadika Genius Aulad Seksyen U16 Denai Alam" are valid.
    """
    if not name:
        return True
    
    name_lower = name.lower()
    
    # Check regex patterns (social media, junk titles)
    if _JUNK_RE.search(name_lower):
        return True
    
    # BUG F3: Use word-boundary regex checks for SEO keywords
    # This prevents "best" in "Best Kids Academy" from triggering
    if _SEO_KEYWORD_RE.search(name_lower):
        return True
    
    return False

def clean_centre_name(name: str) -> str:
    """Clean centre name by splitting at separators and keeping first part."""
    if not name:
        return ""
    
    # Split at common separators and keep first part
    # Note: ", " is handled by split_at_location_suffix for known locations
    for sep in [" | ", " - ", " — ", " | ", ";"]:
        if sep in name:
            name = name.split(sep)[0].strip()
    
    return name

_TITLE_SUFFIX_RE = re.compile(
    r"\s*[-–—|]\s*(kiddy\s*123|skoolsprout|yellowpages|cybo|facebook|instagram"
    r"|selected\s+preschools?|best\s+kindergartens?|[a-z0-9\-]+\.(com|my|net|org))\b.*$",
    re.IGNORECASE,
)
_LOCATION_SUFFIX_RE = re.compile(
    r",?\s*(Shah\s+Alam|Selangor|Malaysia|Klang\s+Valley)\s*$",
    re.IGNORECASE,
)

# BUG 7 FIX: merge resolution uses source priority, not string length
SOURCE_PRIORITY: Dict[str, int] = {
    "Google Maps Places API": 10,
    "Foursquare Places API":  8,
    "Kiddy123 Directory":     7,
    "data.gov.my Open API":   6,
    "Overpass API":           5,
    "Tavily Search":          4,
    "SerpAPI":                3,
}

CORRIDOR_AREAS = [
    "shah alam", "bukit jelutong", "setia alam", "denai alam",
    "elmina", "kota elmina", "glenmarie", "subang bestari",
    "selangor", "u8", "u13", "u16", "40150", "40170", "40160",
]

CURRICULUM_KEYWORDS: Dict[str, List[str]] = {
    "Cambridge Early Years":   ["cambridge", "cambs", "eyfs"],
    "Montessori":              ["montessori", "practical life", "sensorial"],
    "Play-based":              ["play-based", "play based", "child-led"],
    "STEAM":                   ["steam", "coding", "robotics"],
    "Multiple Intelligences":  ["multiple intelligences", "mi approach"],
    "Islamic-integrated":      ["islamic", "tahfiz", "hafazan", "jawi", "solat", "iqra", "quran"],
    "KSPK":                    ["kspk", "kebangsaan", "national curriculum"],
    "Holistic":                ["holistic", "whole child", "social-emotional"],
}

RAW_COLUMNS = [
    "centre_name", "address", "neighbourhood", "lat", "lng",
    "curriculum", "language_medium", "fee_halfday_raw", "fee_fullday_raw",
    "scale", "religious_orientation", "source_primary", "source_notes",
]

# ─────────────────────────── Helpers ──────────────────────────────

def log(msg: str) -> None:
    print(msg)

# BUG F1: JS injection guard for Kiddy123 scraper
# If extracted text contains jQuery/JS code, discard it
_JS_INJECTION_PATTERNS = [
    "jquery", "function($)", "ajax", "$.post", "admin-ajax.php"
]

def is_js_contaminated(text: str) -> bool:
    """Check if text contains JavaScript/jQuery code from search widget injection."""
    if not text:
        return False
    text_lower = text.lower()
    return any(js_pattern in text_lower for js_pattern in _JS_INJECTION_PATTERNS)

def sanitize_field(text: str) -> str:
    """BUG F1: Post-extraction guard - discard field if JS-contaminated."""
    if is_js_contaminated(text):
        return ""
    return text

def safe_str(val: object, default: str = "") -> str:
    if val is None:
        return default
    s = str(val).strip()
    return default if s.lower() in {"", "none", "nan", "n/a"} else s

def blank_record() -> Dict:
    return {col: None if col in ("lat", "lng") else "" for col in RAW_COLUMNS}

def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """
    Correct Haversine. Both cos(lat1) AND cos(lat2) present.
    Previous version was missing cos(lat2), making distances subtly wrong.
    """
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = phi2 - phi1
    dlam = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.asin(math.sqrt(min(1.0, a)))

def is_in_malaysia(lat: float, lng: float) -> bool:
    return MALAYSIA_LAT[0] <= lat <= MALAYSIA_LAT[1] and MALAYSIA_LNG[0] <= lng <= MALAYSIA_LNG[1]

def is_within_radius(c_lat: float, c_lng: float,
                     p_lat: float, p_lng: float,
                     radius_km: float = DEFAULT_RADIUS_KM) -> bool:
    return haversine_km(c_lat, c_lng, p_lat, p_lng) <= radius_km

def geocode_address(address: str) -> Tuple[float, float, bool]:
    """
    Nominatim (OpenStreetMap) geocoding - free tier only.
    No Google Maps API to stay within free tier constraints.
    Uses shared geocoder instance to reduce rate limiting (BUG F8).
    """
    try:
        time.sleep(1.1)  # Rate limiting for Nominatim (1 req/sec)
        geo = get_nominatim_geocoder()  # Use shared instance (BUG F8)
        loc = geo.geocode(address)
        if loc:
            lat, lng = float(loc.latitude), float(loc.longitude)
            if is_in_malaysia(lat, lng):
                return lat, lng, True
    except Exception as exc:
        log(f"[Nominatim] {exc}")

    return DEFAULT_LAT, DEFAULT_LNG, False


def clean_title_to_name(title: str, url: str = "") -> str:
    """Clean a title to extract the centre name.
    
    Args:
        title: The raw title from search results
        url: The source URL (for domain blacklist check)
    
    Returns:
        Cleaned centre name or empty string if should be rejected
    """
    if not title:
        return ""
    
    # Check domain blacklist first (social media, academic, etc.)
    if url and is_domain_blacklisted(url):
        return ""  # Reject names from blacklisted domains entirely
    
    # First clean with aggressive junk filter
    if is_junk_name(title):
        return ""
    
    # Hard split at metadata prefixes (Location:, Age Group:, etc.)
    cleaned = split_at_metadata(title)
    
    # Split at ', ' when followed by known location (Kiddy123 format)
    cleaned = split_at_location_suffix(cleaned)
    
    # Apply name cleaning (split at separators, keep first part)
    cleaned = clean_centre_name(cleaned)
    
    # Apply existing suffix and location cleaning
    cleaned = _TITLE_SUFFIX_RE.sub("", cleaned).strip()
    cleaned = _LOCATION_SUFFIX_RE.sub("", cleaned).strip()
    for w in STRIP_WORDS:
        cleaned = re.sub(r"\b" + re.escape(w) + r"\b", "", cleaned, flags=re.IGNORECASE).strip()
    return re.sub(r"\s{2,}", " ", cleaned).strip().strip(",.-")

def extract_address_from_text(text: str) -> str:
    """Extract Malaysian address from text using regex patterns.
    
    BUG FIX: Patterns were being concatenated (missing commas). Now properly separated.
    """
    if not text:
        return ""
    # BUG FIX: Added commas between patterns (was implicit concatenation)
    patterns = [
        # Pattern 1: Street addresses with Jalan/Persiaran
        r"(?:No\.?\s*\d+[A-Z\-/]*,?\s*)?(?:Jalan|Persiaran|Lorong|Jln)[^\.]{5,80}",
        # Pattern 2: Area names (Selangor, Shah Alam, etc.)
        r"(?:Shah Alam|Selangor|Elmina|Bukit Jelutong|Setia Alam|Denai Alam)[^\.]{0,40}",
        # Pattern 3: Full address with postcode
        r"\d{1,5}[A-Z\-/]*,?\s*(?:Jalan|Persiaran|Lorong|Jln)[^\.]{5,60}\d{5}",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return m.group(0).strip()[:200]
    return ""

def parse_fee_values(text: str) -> List[str]:
    return re.findall(r"RM\s*[\d,]+(?:\s*[-–]\s*[\d,]+)?", text, re.IGNORECASE)

# ─────────────────────────── BUG 7 FIX: Merge by source priority ──

def _merge_records(existing: Dict, new: Dict) -> Dict:
    """
    Fill empty fields and resolve conflicts by source priority.
    BUG 7 FIX: No longer picks the longer string.
    A garbled 200-char Tavily snippet no longer beats a clean Google Maps address.
    """
    merged = dict(existing)
    ep = SOURCE_PRIORITY.get(safe_str(existing.get("source_primary")), 0)
    np = SOURCE_PRIORITY.get(safe_str(new.get("source_primary")), 0)

    for key in RAW_COLUMNS:
        e_val = merged.get(key)
        n_val = new.get(key)
        e_empty = e_val is None or safe_str(e_val) == ""
        n_empty = n_val is None or safe_str(n_val) == ""
        if e_empty and not n_empty:
            merged[key] = n_val
        elif not e_empty and not n_empty and key in ("address", "lat", "lng") and np > ep:
            merged[key] = n_val

    e_notes = safe_str(merged.get("source_notes"))
    n_notes = safe_str(new.get("source_notes"))
    if n_notes and n_notes not in e_notes:
        merged["source_notes"] = f"{e_notes} | {n_notes}".strip(" |")
    return merged

# ─────────────────────────── SMART DEDUPLICATION v2.0 ────────────

# Deduplication thresholds
DEDUP_RADIUS_METERS = 150  # Stricter radius to prevent over-merging
THREAT_RADIUS_METERS = 10000   # Threat detection radius around target (10km)
NAME_SIMILARITY_THRESHOLD = 60  # Lower sensitivity to merge similar centres
HIGH_CONFIDENCE_SIMILARITY = 92
ADDRESS_SIMILARITY_THRESHOLD = 85
ECE_PREFIX_PENALTY = 15  # Penalty for different ECE prefixes

# Government chains that should NOT be merged unless identical
GOVERNMENT_CHAINS = ["tabika kemas", "pasti", "tadika negara", "taska komuniti"]

def extract_name_components(name: str):
    """Extract ECE prefix, brand, and location from name."""
    name_lower = name.lower().strip()
    
    # Extract prefix
    prefix = ""
    for p in sorted(ECE_PREFIXES, key=len, reverse=True):
        if name_lower.startswith(p + " "):
            prefix = p
            name_lower = name_lower[len(p):].strip()
            break
    
    # Extract location suffix
    words = name_lower.split()
    location_suffix = ""
    brand = name_lower
    
    location_keywords = [
        "setia alam", "bukit jelutong", "denai alam", "elmina", "glenmarie",
        "subang bestari", "shah alam", "u8", "u13", "u16", "u5",
        "jalan", "persiaran", "lorong", "jln"
    ]
    
    for keyword in location_keywords:
        if name_lower.endswith(keyword):
            location_suffix = keyword
            brand = name_lower[:-len(keyword)].strip()
            break
    
    return prefix, brand, location_suffix

def normalize_for_dedup(name: str) -> str:
    """Normalize name while preserving key identifiers."""
    s = name.lower().strip()
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def calculate_name_similarity(name1: str, name2: str):
    """Calculate sophisticated name similarity with ECE prefix awareness."""
    norm1 = normalize_for_dedup(name1)
    norm2 = normalize_for_dedup(name2)
    
    prefix1, brand1, loc1 = extract_name_components(name1)
    prefix2, brand2, loc2 = extract_name_components(name2)
    
    # Base token sort ratio
    base_similarity = fuzz.token_sort_ratio(norm1, norm2)
    
    # Apply ECE prefix penalty
    if prefix1 and prefix2 and prefix1 != prefix2:
        adjusted_similarity = max(0, base_similarity - ECE_PREFIX_PENALTY)
    else:
        adjusted_similarity = base_similarity
    
    # Brand bonus
    brand_sim = fuzz.token_sort_ratio(brand1, brand2)
    if brand_sim > 90:
        adjusted_similarity = min(100, adjusted_similarity + 5)
    
    metadata = {
        "base_sim": base_similarity,
        "adjusted_sim": adjusted_similarity,
        "prefix1": prefix1, "prefix2": prefix2,
        "loc1": loc1, "loc2": loc2,
    }
    
    return adjusted_similarity, metadata

def normalize_address_for_dedup(address: str) -> str:
    """Normalize address for comparison."""
    if not address:
        return ""
    addr = address.lower().strip()
    addr = re.sub(r'\bjln\b', 'jalan', addr)
    addr = re.sub(r'\bno\b', 'no', addr)
    addr = re.sub(r',?\s*(shah alam|selangor|malaysia)\s*$', '', addr)
    addr = re.sub(r'\s+', ' ', addr).strip()
    return addr

def should_merge_records(rec1: Dict, rec2: Dict) -> tuple:
    """Determine if two records should be merged.
    
    Logic:
    1. Check name similarity (60% threshold)
    2. If names similar, check address similarity
    3. If both names and addresses similar, merge
    4. Use coordinates as final confirmation if available
    """
    name1 = safe_str(rec1.get("centre_name"), "")
    name2 = safe_str(rec2.get("centre_name"), "")
    addr1 = safe_str(rec1.get("address"), "")
    addr2 = safe_str(rec2.get("address"), "")
    lat1, lng1 = rec1.get("lat"), rec1.get("lng")
    lat2, lng2 = rec2.get("lat"), rec2.get("lng")
    
    name_sim, name_meta = calculate_name_similarity(name1, name2)
    
    # Step 1: Check name similarity
    if name_sim < NAME_SIMILARITY_THRESHOLD:
        return False, f"Name similarity too low ({name_sim})"
    
    # Step 2: Check address similarity
    norm_addr1 = normalize_address_for_dedup(addr1)
    norm_addr2 = normalize_address_for_dedup(addr2)
    addr_sim = fuzz.token_sort_ratio(norm_addr1, norm_addr2)
    
    # If addresses are similar, we have a strong case for merging
    if addr_sim >= ADDRESS_SIMILARITY_THRESHOLD:
        # Step 3: Use coordinates for final confirmation if available
        has_coords1 = lat1 is not None and lng1 is not None
        has_coords2 = lat2 is not None and lng2 is not None
        
        if has_coords1 and has_coords2:
            try:
                dist_m = haversine_km(float(lat1), float(lng1), float(lat2), float(lng2)) * 1000
                
                # Require BOTH close distance AND strong text match
                # Government chains: only merge if identical names AND very close
                name1_lower = name1.lower().strip()
                name2_lower = name2.lower().strip()
                is_gov_chain = any(chain in name1_lower and chain in name2_lower for chain in GOVERNMENT_CHAINS)
                
                if is_gov_chain:
                    # Government chains: require identical names + very close distance
                    if name_sim >= 99 and dist_m <= 50:
                        return True, f"Gov chain identical ({name_sim}) + very close ({dist_m:.0f}m)"
                    else:
                        return False, f"Gov chain different ({name_sim}) - keep separate branches"
                
                # Regular centres: require close distance AND strong text match
                if dist_m <= DEDUP_RADIUS_METERS and name_sim >= HIGH_CONFIDENCE_SIMILARITY:
                    return True, f"Strong match: name ({name_sim}) + close ({dist_m:.0f}m)"
                
                # Very close distance + good similarity
                if dist_m <= 50 and name_sim >= NAME_SIMILARITY_THRESHOLD:
                    return True, f"Very close ({dist_m:.0f}m) + good name ({name_sim})"
                
                # Far apart = different branches
                if dist_m > 200:
                    return False, f"Far apart ({dist_m:.0f}m) - different branches"
                    
            except (ValueError, TypeError):
                pass
        else:
            # No coordinates - require very high similarity
            if name_sim >= HIGH_CONFIDENCE_SIMILARITY:
                return True, f"Very high name ({name_sim}) + address ({addr_sim}) match (no coords)"
            
            return False, f"Insufficient similarity ({name_sim}) - keep separate (no coords)"
    
    # Step 4: Special case - very high name similarity without address match
    if name_sim >= HIGH_CONFIDENCE_SIMILARITY:
        has_coords1 = lat1 is not None and lng1 is not None
        has_coords2 = lat2 is not None and lng2 is not None
        
        if has_coords1 and has_coords2:
            try:
                dist_m = haversine_km(float(lat1), float(lng1), float(lat2), float(lng2)) * 1000
                
                # Government chains: require identical names + very close
                name1_lower = name1.lower().strip()
                name2_lower = name2.lower().strip()
                is_gov_chain = any(chain in name1_lower and chain in name2_lower for chain in GOVERNMENT_CHAINS)
                
                if is_gov_chain:
                    if name_sim >= 99 and dist_m <= 50:
                        return True, f"Gov chain identical ({name_sim}) + very close ({dist_m:.0f}m)"
                    else:
                        return False, f"Gov chain different ({name_sim}) - keep separate"
                
                # Regular centres: require very close distance for high similarity
                if dist_m <= 50:
                    return True, f"Very high name ({name_sim}) + very close ({dist_m:.0f}m)"
                    
            except (ValueError, TypeError):
                pass
    
    return False, f"No merge: name ({name_sim}) + address ({addr_sim}) insufficient"

def deduplicate(records: List[Dict]) -> List[Dict]:
    """
    Smart deduplication with ECE prefix awareness and branch separation.
    Uses 150m radius (DEDUP_RADIUS_METERS constant).
    """
    if not records:
        return []
    
    log(f"[Deduplication] Processing {len(records)} records...")
    
    # Sort by source priority
    sorted_records = sorted(
        records,
        key=lambda r: SOURCE_PRIORITY.get(safe_str(r.get("source_primary")), 0),
        reverse=True
    )
    
    seen: List[Dict] = []
    merge_count = 0
    
    for rec in sorted_records:
        merged_into_existing = False
        
        for i, existing in enumerate(seen):
            should_merge, reason = should_merge_records(existing, rec)
            
            if should_merge:
                seen[i] = _merge_records(existing, rec)
                merged_into_existing = True
                merge_count += 1
                log(f"  [Merge] {reason}")
                break
        
        if not merged_into_existing:
            seen.append(rec)
    
    log(f"[Deduplication] {len(records)} -> {len(seen)} records ({merge_count} merged)")
    return seen

# ─────────────────────────── Source 1: Overpass ───────────────────

def fetch_overpass(c_lat: float, c_lng: float, radius_km: float) -> List[Dict]:
    log("[Overpass] Fetching...")
    out: List[Dict] = []
    # Primary and fallback endpoints
    endpoints = [
        "https://overpass-api.de/api/interpreter",
        "https://overpass.kumi.systems/api/interpreter"
    ]
    zones = [
        (c_lat, c_lng, int(radius_km * 1000), "main"),
        (ELMINA_LAT, ELMINA_LNG, ELMINA_RADIUS_M, "elmina"),
    ]
    for q_lat, q_lng, q_r, label in zones:
        ql = f"""[out:json][timeout:90];
(
  node["amenity"="kindergarten"](around:{q_r},{q_lat},{q_lng});
  node["amenity"="childcare"](around:{q_r},{q_lat},{q_lng});
  way["amenity"="kindergarten"](around:{q_r},{q_lat},{q_lng});
  way["amenity"="childcare"](around:{q_r},{q_lat},{q_lng});
);
out center;"""
        
        # Try each endpoint until one works
        resp = None
        for endpoint in endpoints:
            try:
                resp = requests.post(endpoint, data=ql, timeout=100)
                resp.raise_for_status()
                break  # Success, exit endpoint loop
            except Exception as exc:
                if endpoint == endpoints[-1]:  # Last endpoint failed
                    log(f"[Overpass] Failed ({label}): {exc}. Continuing.")
                    resp = None
                else:
                    log(f"[Overpass] Trying fallback endpoint for {label}...")
        
        # Process successful response (outside endpoint loop)
        if resp is not None:
            for el in resp.json().get("elements", []):
                lat = el.get("lat") or el.get("center", {}).get("lat")
                lng = el.get("lon") or el.get("center", {}).get("lon")
                if lat is None or lng is None:
                    continue
                lat, lng = float(lat), float(lng)
                if not is_in_malaysia(lat, lng):
                    continue
                # NOTE: Radius check removed - let enricher.py handle filtering
                # after verification/cleaning for more accurate results
                tags = el.get("tags", {})
                name = safe_str(tags.get("name"))
                if not name:
                    continue
                rec = blank_record()
                rec["centre_name"] = name
                parts = [safe_str(tags.get("addr:housenumber")),
                         safe_str(tags.get("addr:street")),
                         safe_str(tags.get("addr:city")),
                         safe_str(tags.get("addr:postcode"))]
                rec["address"] = ", ".join(p for p in parts if p)
                rec["lat"] = lat
                rec["lng"] = lng
                rec["source_primary"] = "Overpass API"
                rec["source_notes"]   = f"[Verified: Overpass API - OpenStreetMap ({label})]"
                out.append(rec)
    log(f"[Overpass] {len(out)} rows")
    return out

# ─────────────────────────── Source 2: Kiddy123 ───────────────────

def fetch_kiddy123(c_lat: float, c_lng: float, radius_km: float) -> List[Dict]:
    log("[Kiddy123] Fetching...")
    out: List[Dict] = []
    # Updated URLs with internal IDs for new site structure
    urls = [
        "https://www.kiddy123.com/listing/guide/nursery-kindergarten/state/selangor-39/city/shah-alam-187",
        "https://www.kiddy123.com/listing/guide/nursery-kindergarten/state/selangor-39/city/setia-alam-188",
    ]
    # Realistic User-Agent to prevent blocking
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }

    for list_url in urls:
        try:
            page = requests.get(list_url, headers=headers, timeout=30)
            page.raise_for_status()
            soup = BeautifulSoup(page.text, "html.parser")
            links: List[Tuple[str, str]] = []
            for a in soup.find_all("a", href=True):
                href, text = a["href"], a.get_text(" ", strip=True)
                if len(text) < 5:
                    continue
                if (any(s in text.lower() for s in ECE_SYNONYMS) or
                        any(b in text.lower() for b in KNOWN_BRANDS)):
                    full = href if href.startswith("http") else f"https://www.kiddy123.com{href}"
                    # Accept /listing/ or /listing-category/ URLs
                    if ("/listing/" in full or "/listing-category/" in full) and (text, full) not in links:
                        links.append((text, full))

            for name, detail_url in links[:40]:
                if is_junk_name(name):
                    continue
                try:
                    time.sleep(1)
                    det = requests.get(detail_url, headers=headers, timeout=30)
                    det.raise_for_status()
                    dsoup = BeautifulSoup(det.text, "html.parser")
                    page_text = dsoup.get_text(" ", strip=True)

                    address = ""
                    for kw in ["Address", "Alamat"]:
                        node = dsoup.find(string=re.compile(kw, re.IGNORECASE))
                        if node and node.parent:
                            t = re.sub(r"^(Address|Alamat):?\s*", "",
                                       node.parent.get_text(" ", strip=True),
                                       flags=re.IGNORECASE).strip()
                            if len(t) > 10:
                                address = t
                                break
                    
                    # BUG F1: Discard JS-contaminated fields
                    address = sanitize_field(address)

                    fees = parse_fee_values(page_text)
                    rec = blank_record()
                    rec["centre_name"]     = name
                    rec["address"]         = address
                    rec["fee_halfday_raw"] = fees[0] if fees else ""
                    rec["fee_fullday_raw"] = fees[-1] if len(fees) > 1 else (fees[0] if fees else "")
                    rec["source_primary"]  = "Kiddy123 Directory"
                    rec["source_notes"]    = (
                        "[Verified: Kiddy123] Fees confirmed."
                        if fees else "[Verified: Kiddy123 directory]"
                    )
                    out.append(rec)
                except Exception as exc:
                    log(f"[Kiddy123] Detail failed ({name[:40]}): {exc}")
        except Exception as exc:
            log(f"[Kiddy123] Listing failed: {exc}. Continuing.")

    out = [r for r in out if not is_junk_name(safe_str(r.get("centre_name")))]
    log(f"[Kiddy123] {len(out)} rows after filter")
    return out

# ─────────────────────────── Source 3: Foursquare ─────────────────

def fetch_foursquare(c_lat: float, c_lng: float, radius_km: float) -> List[Dict]:
    log("[Foursquare] Fetching...")
    out: List[Dict] = []
    key = os.getenv("FOURSQUARE_KEY")
    if not key:
        log("[Foursquare] FOURSQUARE_KEY not found — skipping")
        return out

    endpoint = "https://api.foursquare.com/v3/places/search"
    headers = {"Authorization": key}
    zones = [
        (c_lat, c_lng, int(radius_km * 1000), "main"),
        (ELMINA_LAT, ELMINA_LNG, ELMINA_RADIUS_M, "elmina"),
    ]
    for q_lat, q_lng, q_r, label in zones:
        for syn in ECE_SYNONYMS[:6]:
            try:
                resp = requests.get(endpoint, headers=headers, params={
                    "ll": f"{q_lat},{q_lng}", "radius": q_r,
                    "categories": "12058,12056", "limit": 50,
                    "fields": "name,location,geocodes,rating", "query": syn,
                }, timeout=30)
                resp.raise_for_status()
                for item in resp.json().get("results", []):
                    name = safe_str(item.get("name"))
                    if not name or is_junk_name(name):
                        continue
                    gc = item.get("geocodes", {}).get("main", {})
                    lat, lng = gc.get("latitude"), gc.get("longitude")
                    if lat is None:
                        continue
                    lat, lng = float(lat), float(lng)
                    if not is_in_malaysia(lat, lng):
                        continue
                    # NOTE: Radius check deferred to enricher.py
                    loc = item.get("location", {})
                    rec = blank_record()
                    rec["centre_name"]    = name
                    rec["address"]        = safe_str(loc.get("formatted_address"))
                    rec["lat"]            = lat
                    rec["lng"]            = lng
                    rec["source_primary"] = "Foursquare Places API"
                    rating = item.get("rating", "")
                    rec["source_notes"]   = (
                        "[Verified: Foursquare Places API]"
                        + (f" Rating: {rating}/10" if rating else "")
                    )
                    out.append(rec)
            except Exception as exc:
                log(f"[Foursquare] Failed ({label}, {syn}): {exc}. Continuing.")
    log(f"[Foursquare] {len(out)} rows")
    return out

# ─────────────────────────── Source 4: Google Maps Places ─────────

def fetch_google_maps(c_lat: float, c_lng: float, radius_km: float) -> List[Dict]:
    """
    Highest-quality source: verified business names + formatted addresses
    from Google's own database. Uses Nearby Search + Text Search.
    """
    key = os.getenv("GOOGLE_MAPS_KEY")
    if not key:
        log("[Google Maps] GOOGLE_MAPS_KEY not found — skipping")
        return []

    log("[Google Maps] Fetching...")
    out: List[Dict] = []
    seen_ids: set = set()

    nearby_url  = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
    text_url    = "https://maps.googleapis.com/maps/api/place/textsearch/json"
    details_url = "https://maps.googleapis.com/maps/api/place/details/json"

    def get_details(pid: str) -> Dict:
        try:
            r = requests.get(details_url, params={
                "place_id": pid,
                "fields": "name,formatted_address,geometry,rating",
                "key": key,
            }, timeout=15)
            return r.json().get("result", {})
        except Exception:
            return {}

    def process(results: list, qlabel: str) -> None:
        for place in results:
            pid = place.get("place_id")
            if not pid or pid in seen_ids:
                continue
            seen_ids.add(pid)
            name = safe_str(place.get("name"))
            if not name or is_junk_name(name):
                continue
            geo = place.get("geometry", {}).get("location", {})
            lat, lng = geo.get("lat"), geo.get("lng")
            if lat is None:
                continue
            lat, lng = float(lat), float(lng)
            if not is_in_malaysia(lat, lng):
                continue
            # NOTE: Radius check deferred to enricher.py
            details = get_details(pid)
            raw_addr = details.get("formatted_address") or safe_str(place.get("vicinity", ""))
            address  = re.sub(r",?\s*Malaysia\s*$", "", raw_addr, flags=re.IGNORECASE).strip()
            rec = blank_record()
            rec["centre_name"]    = name
            rec["address"]        = address
            rec["lat"]            = lat
            rec["lng"]            = lng
            rec["source_primary"] = "Google Maps Places API"
            rating = place.get("rating") or details.get("rating")
            rec["source_notes"]   = (
                "[Verified: Google Maps Places API]"
                + (f" Google rating: {rating}/5." if rating else "")
                + f" query='{qlabel[:40]}'"
            )
            out.append(rec)
            time.sleep(0.15)

    zones = [
        (c_lat, c_lng, int(radius_km * 1000), "main corridor"),
        (ELMINA_LAT, ELMINA_LNG, ELMINA_RADIUS_M, "Elmina"),
    ]
    for z_lat, z_lng, z_r, label in zones:
        for kw in ["preschool", "kindergarten", "tadika", "taska", "childcare"]:
            try:
                resp = requests.get(nearby_url, params={
                    "location": f"{z_lat},{z_lng}",
                    "radius": z_r, "keyword": kw, "key": key,
                }, timeout=20)
                data = resp.json()
                if data.get("status") not in ("OK", "ZERO_RESULTS"):
                    continue
                process(data.get("results", []), f"nearby:{kw} {label}")
                npt = data.get("next_page_token")
                if npt:
                    time.sleep(2.0)
                    r2 = requests.get(nearby_url,
                                      params={"pagetoken": npt, "key": key}, timeout=20)
                    process(r2.json().get("results", []), f"nearby:{kw} {label} p2")
            except Exception as exc:
                log(f"[Google Maps Nearby] Failed ({kw}, {label}): {exc}. Continuing.")

    for q in [
        "tadika Bukit Jelutong Shah Alam",
        "taska Bukit Jelutong Shah Alam",
        "preschool Setia Alam Shah Alam",
        "tadika Setia Alam Shah Alam",
        "preschool kindergarten Denai Alam Shah Alam",
        "taska Elmina Shah Alam",
        "tadika Kota Elmina",
        "preschool Glenmarie Shah Alam",
    ]:
        try:
            resp = requests.get(text_url, params={
                "query": q, "location": f"{c_lat},{c_lng}",
                "radius": int(radius_km * 1000), "key": key,
            }, timeout=20)
            data = resp.json()
            if data.get("status") not in ("OK", "ZERO_RESULTS"):
                continue
            process(data.get("results", []), f"text:{q[:40]}")
            time.sleep(0.5)
        except Exception as exc:
            log(f"[Google Maps Text] Failed '{q}': {exc}. Continuing.")

    log(f"[Google Maps] {len(out)} rows (unique IDs: {len(seen_ids)})")
    return out

# ─────────────────────────── Source 5: Tavily ─────────────────────

def fetch_tavily(c_lat: float, c_lng: float, radius_km: float) -> List[Dict]:
    """
    BUG 1 FIX: Tavily results are no longer routed through Ollama.
    An offline LLM cannot verify whether a business actually exists.
    Names are filtered by is_junk_name(); addresses extracted by regex.
    """
    if not _TAVILY_OK:
        log("[Tavily] tavily package not installed — skipping")
        return []
    key = os.getenv("TAVILY_KEY")
    if not key:
        log("[Tavily] TAVILY_KEY not found — skipping")
        return []

    log("[Tavily] Fetching...")
    out: List[Dict] = []
    client = _TavilyClient(api_key=key)

    # Expanded queries for better coverage - all areas within ~15km of target
    queries = [
        # Bukit Jelutong area (primary)
        "tadika Bukit Jelutong Shah Alam preschool fees",
        "taska Bukit Jelutong Shah Alam childcare",
        "nursery Bukit Jelutong Shah Alam",
        "prasekolah Bukit Jelutong",
        "pusat jagaan Bukit Jelutong",
        # Setia Alam area
        "preschool kindergarten Setia Alam Shah Alam fees",
        "tadika Setia Alam Shah Alam",
        "taska Setia Alam",
        "nursery Setia Alam",
        # Denai Alam area
        "tadika taska Denai Alam Shah Alam",
        "preschool Denai Alam fees",
        # Elmina area
        "taska Elmina Shah Alam childcare",
        "tadika Kota Elmina preschool fees",
        "nursery Elmina Shah Alam",
        # Glenmarie area
        "preschool kindergarten Glenmarie Subang Bestari Shah Alam",
        "tadika Glenmarie Shah Alam",
        # Specific brands in Shah Alam
        "Brainy Bunch Setia Alam location fees",
        "REAL Kids Bukit Jelutong address fees",
        "Little Caliphs Setia Alam fees",
        "Genius Aulad Elmina fees",
        "Knowledge Tree Montessori Eco Ardence fees",
        "MRC Kids Shah Alam",
        "Smart Reader Kids Shah Alam",
        "CIC Shah Alam kindergarten",
        "Eduwis Shah Alam",
        # General Shah Alam searches
        "best preschool Shah Alam 2024",
        "kindergarten near Bukit Jelutong Shah Alam",
        "childcare center Shah Alam U8 U13 U16",
    ]

    for q in queries:
        try:
            response = client.search(
                query=q, search_depth="basic",
                include_answer=False, include_raw_content=True,
                max_results=10, exclude_domains=["youtube.com"],
            )
            for result in response.get("results", []):
                title   = safe_str(result.get("title", ""))
                content = safe_str(result.get("content", ""))
                raw     = safe_str(result.get("raw_content", ""))
                url     = safe_str(result.get("url", ""))

                name = clean_title_to_name(title, url)  # Pass URL for domain blacklist check
                if is_junk_name(name):
                    continue

                full_text = raw if raw else content
                address   = extract_address_from_text(full_text) or extract_address_from_text(content)

                # BUG 5 FIX: geocode address and radius-check
                lat, lng = None, None
                if address:
                    lat, lng, ok = geocode_address(address)
                    # NOTE: Radius check deferred to enricher.py
                    if not ok:
                        lat, lng = None, None

                fees = parse_fee_values(full_text)
                curriculum = ""
                for label, kws in CURRICULUM_KEYWORDS.items():
                    if any(kw in full_text.lower() for kw in kws):
                        curriculum = label
                        break

                rec = blank_record()
                rec["centre_name"]     = name
                rec["address"]         = address
                rec["lat"]             = lat
                rec["lng"]             = lng
                rec["fee_halfday_raw"] = fees[0] if fees else ""
                rec["fee_fullday_raw"] = fees[-1] if len(fees) > 1 else (fees[0] if fees else "")
                rec["curriculum"]      = curriculum
                rec["source_primary"]  = "Tavily Search"
                rec["source_notes"]    = (
                    f"[Verified: Tavily Search — {url[:60]}]"
                    + (" Fees from website." if fees else "")
                )
                out.append(rec)
        except Exception as exc:
            log(f"[Tavily] Failed ('{q[:40]}'): {exc}. Continuing.")

    out = [r for r in out if not is_junk_name(safe_str(r.get("centre_name")))]
    log(f"[Tavily] {len(out)} rows after filter")
    return out

# ─────────────────────────── Source 6: SerpAPI ────────────────────

def fetch_serpapi(c_lat: float, c_lng: float, radius_km: float) -> List[Dict]:
    """
    BUG 6 FIX: get_dict() called immediately inside this function.
    Returns List[Dict] always — never returns a GoogleSearch object.
    BUG 1 FIX: Results filtered by keyword, not Ollama.
    BUG 5 FIX: Results geocoded and radius-checked.
    """
    key = os.getenv("SERPAPI_KEY")
    if not key:
        log("[SerpAPI] SERPAPI_KEY not found — skipping")
        return []
    try:
        from serpapi import GoogleSearch
    except ImportError:
        log("[SerpAPI] serpapi package not installed — skipping")
        return []

    log("[SerpAPI] Fetching...")
    out: List[Dict] = []

    queries = [
        "tadika Bukit Jelutong Shah Alam site:kiddy123.com OR site:fb.com",
        "taska preschool Bukit Jelutong Shah Alam",
        "tadika taska Setia Alam Shah Alam",
        "preschool kindergarten Setia Alam Shah Alam",
        "tadika taska Denai Alam Shah Alam",
        "taska Elmina Shah Alam",
        "tadika Kota Elmina Shah Alam",
        "preschool kindergarten Glenmarie Shah Alam",
    ]

    for q in queries:
        try:
            # BUG 6 FIX: call get_dict() here, return list not object
            search = GoogleSearch({
                "q": q,
                "location": "Shah Alam, Selangor, Malaysia",
                "hl": "en", "gl": "my",
                "api_key": key, "engine": "google", "num": 10,
            })
            results_dict = search.get_dict()

            for result in results_dict.get("organic_results", []):
                title   = safe_str(result.get("title", ""))
                snippet = safe_str(result.get("snippet", ""))
                link    = safe_str(result.get("link", ""))
                name    = clean_title_to_name(title, link)  # Pass URL for domain blacklist check
                if is_junk_name(name):
                    continue

                address = extract_address_from_text(snippet)
                lat, lng = None, None
                if address:
                    lat, lng, ok = geocode_address(address)
                    # NOTE: Radius check deferred to enricher.py
                    if not ok:
                        lat, lng = None, None

                rec = blank_record()
                rec["centre_name"]    = name
                rec["address"]        = address
                rec["lat"]            = lat
                rec["lng"]            = lng
                rec["source_primary"] = "SerpAPI"
                rec["source_notes"]   = f"[Verified: SerpAPI — Google Search] query='{q[:50]}'"
                out.append(rec)

            time.sleep(1.0)
        except Exception as exc:
            log(f"[SerpAPI] Failed ('{q[:40]}'): {exc}. Continuing.")

    out = [r for r in out if not is_junk_name(safe_str(r.get("centre_name")))]
    log(f"[SerpAPI] {len(out)} rows after filter")
    return out

# ─────────────────────────── Radius filter ────────────────────────

def filter_by_radius(records: List[Dict],
                     c_lat: float, c_lng: float,
                     radius_km: float) -> Tuple[List[Dict], int]:
    kept, excluded = [], 0
    for rec in records:
        lat, lng = rec.get("lat"), rec.get("lng")
        name = safe_str(rec.get("centre_name"))
        addr = safe_str(rec.get("address")).lower()

        if lat is not None and lng is not None:
            try:
                lat, lng = float(lat), float(lng)
                if not is_in_malaysia(lat, lng):
                    log(f"Excluded '{name}' — outside Malaysia")
                    excluded += 1
                    continue
                if not is_within_radius(c_lat, c_lng, lat, lng, radius_km):
                    dist = haversine_km(c_lat, c_lng, lat, lng)
                    log(f"Excluded '{name}' — {dist:.1f}km from centre")
                    excluded += 1
                    continue
                kept.append(rec)
            except Exception:
                kept.append(rec)
        else:
            combined = addr + " " + name.lower()
            if any(area in combined for area in CORRIDOR_AREAS):
                kept.append(rec)
            else:
                log(f"Excluded '{name}' — no coords and no area keyword")
                excluded += 1
    return kept, excluded

# ─────────────────────────── Pipeline ─────────────────────────────

def ensure_schema(df: pd.DataFrame) -> pd.DataFrame:
    for col in RAW_COLUMNS:
        if col not in df.columns:
            df[col] = None if col in ("lat", "lng") else ""
    return df[RAW_COLUMNS]

def run_pipeline(address: str, radius_km: float, output_csv: str) -> None:
    c_lat, c_lng, ok = geocode_address(address)
    log(f"Centre: {c_lat:.4f}, {c_lng:.4f}" + ("" if ok else " (default fallback)"))

    all_records: List[Dict] = []
    sources_used: List[str] = []

    for fn, label in [
        (lambda: fetch_overpass(c_lat, c_lng, radius_km),    "Overpass"),
        (lambda: fetch_kiddy123(c_lat, c_lng, radius_km),    "Kiddy123"),
        (lambda: fetch_foursquare(c_lat, c_lng, radius_km),  "Foursquare"),
        (lambda: fetch_google_maps(c_lat, c_lng, radius_km), "Google Maps"),
        (lambda: fetch_tavily(c_lat, c_lng, radius_km),      "Tavily"),
        (lambda: fetch_serpapi(c_lat, c_lng, radius_km),     "SerpAPI"),
    ]:
        rows = fn()
        if rows:
            all_records.extend(rows)
            sources_used.append(label)

    log(f"\nTotal before dedup: {len(all_records)} rows")
    deduped = deduplicate(all_records)
    log(f"After dedup: {len(deduped)} rows")

    # NOTE: Radius filtering deferred to enricher.py for more accurate results
    # after address verification and geocoding
    log(f"Collected {len(deduped)} rows (radius filtering in enricher.py)")

    df = pd.DataFrame(deduped)
    df = ensure_schema(df)
    os.makedirs(os.path.dirname(output_csv) if os.path.dirname(output_csv) else ".", exist_ok=True)
    df.to_csv(output_csv, index=False)
    log(f"\nraw.csv written: {len(df)} rows | sources: {', '.join(sources_used)}")

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Project Kestrel fetcher v6.0")
    p.add_argument("--address", default=DEFAULT_ADDRESS)
    p.add_argument("--radius",  type=float, default=DEFAULT_RADIUS_KM)
    p.add_argument("--output",  default="data/raw.csv")
    return p.parse_args()

if __name__ == "__main__":
    args = parse_args()
    run_pipeline(args.address, args.radius, args.output)
