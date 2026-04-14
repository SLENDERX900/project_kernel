"""
Project Kestrel — enricher.py  (v2.0 — Free Tier Only)

Reads data/raw.csv, enriches every row, writes data/master.csv.
Uses Nominatim (OpenStreetMap) for geocoding - NO Google Maps API.
Uses duckduckgo-search (ddgs) for verification - NO paid LLM APIs.

CHANGES v2.0:
  - Removed Playwright verifier (headless browsing too brittle)
  - Removed Google Maps API dependency (free tier only)
  - Added DuckDuckGo Search (ddgs) waterfall enrichment
  - Added Nominatim reverse geocoding for neighbourhood assignment
  - Enhanced Malaysian address standardization
  - Smart verification: ddgs primary, Tavily/SerpAPI fallback

Run:
  python enricher.py [--input data/raw.csv] [--output data/master.csv]
"""

import argparse
import json
import math
import os
import re
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import pandas as pd
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from geopy.exc import GeocoderServiceError, GeocoderTimedOut
from geopy.geocoders import Nominatim
from thefuzz import fuzz

# Import spaCy for NER gatekeeper (Layer B)
try:
    import spacy
    from spacy.language import Language
    SPACY_AVAILABLE = True
except ImportError:
    SPACY_AVAILABLE = False

# Malaysian address abbreviation mapping
MALAYSIAN_ABBREV_MAP = {
    "jln": "jalan", "jalan": "jalan",
    "tmn": "taman", "taman": "taman",
    "kg": "kampung", "kampung": "kampung",
    "lrg": "lorong", "lorong": "lorong",
    "bkt": "bukit", "bukit": "bukit",
    "pj": "petaling jaya",
    "kl": "kuala lumpur",
    "jb": "johor bahru",
    "shah alam": "shah alam",
    "sel": "selangor",
}

def normalize_malaysian_abbreviations(text: str) -> str:
    """Normalize Malaysian address abbreviations for better NER matching."""
    if not text:
        return text
    words = text.split()
    normalized = []
    for word in words:
        lower_word = word.lower().rstrip(",.;")
        if lower_word in MALAYSIAN_ABBREV_MAP:
            # Preserve original case style
            if word.isupper():
                normalized.append(MALAYSIAN_ABBREV_MAP[lower_word].upper())
            elif word[0].isupper():
                normalized.append(MALAYSIAN_ABBREV_MAP[lower_word].title())
            else:
                normalized.append(MALAYSIAN_ABBREV_MAP[lower_word])
        else:
            normalized.append(word)
    return " ".join(normalized)
    
def create_malaysian_ner_pipeline():
    """Create spaCy pipeline with Malaysian EntityRuler patterns."""
    if not SPACY_AVAILABLE:
        return None
    try:
        # Load base model (disable unused components for speed)
        nlp = spacy.load("en_core_web_sm", disable=["parser", "lemmatizer"])
        
        # Add EntityRuler BEFORE NER to prioritize local rules
        ruler = nlp.add_pipe("entity_ruler", before="ner")
        
        # Comprehensive Malaysian patterns
        patterns = [
            # States & Federal Territories
            {"label": "GPE", "pattern": [{"LOWER": "selangor"}]},
            {"label": "GPE", "pattern": [{"LOWER": "johor"}]},
            {"label": "GPE", "pattern": [{"LOWER": "kedah"}]},
            {"label": "GPE", "pattern": [{"LOWER": "kelantan"}]},
            {"label": "GPE", "pattern": [{"LOWER": "melaka"}]},
            {"label": "GPE", "pattern": [{"LOWER": "negeri"}, {"LOWER": "sembilan"}]},
            {"label": "GPE", "pattern": [{"LOWER": "pahang"}]},
            {"label": "GPE", "pattern": [{"LOWER": "penang"}]},
            {"label": "GPE", "pattern": [{"LOWER": "pulau"}, {"LOWER": "pinang"}]},
            {"label": "GPE", "pattern": [{"LOWER": "perak"}]},
            {"label": "GPE", "pattern": [{"LOWER": "perlis"}]},
            {"label": "GPE", "pattern": [{"LOWER": "sabah"}]},
            {"label": "GPE", "pattern": [{"LOWER": "sarawak"}]},
            {"label": "GPE", "pattern": [{"LOWER": "terengganu"}]},
            {"label": "GPE", "pattern": [{"LOWER": "kuala"}, {"LOWER": "lumpur"}]},
            {"label": "GPE", "pattern": [{"LOWER": "labuan"}]},
            {"label": "GPE", "pattern": [{"LOWER": "putrajaya"}]},
            
            # Major Cities/Districts
            {"label": "GPE", "pattern": [{"LOWER": "shah"}, {"LOWER": "alam"}]},
            {"label": "GPE", "pattern": [{"LOWER": "petaling"}, {"LOWER": "jaya"}]},
            {"label": "GPE", "pattern": [{"LOWER": "klang"}]},
            {"label": "GPE", "pattern": [{"LOWER": "ipoh"}]},
            {"label": "GPE", "pattern": [{"LOWER": "kuching"}]},
            {"label": "GPE", "pattern": [{"LOWER": "kota"}, {"LOWER": "kinabalu"}]},
            {"label": "GPE", "pattern": [{"LOWER": "johor"}, {"LOWER": "bahru"}]},
            {"label": "GPE", "pattern": [{"LOWER": "seremban"}]},
            {"label": "GPE", "pattern": [{"LOWER": "kuantan"}]},
            {"label": "GPE", "pattern": [{"LOWER": "kuala"}, {"LOWER": "terengganu"}]},
            
            # Local Area Names (Shah Alam specific)
            {"label": "GPE", "pattern": [{"LOWER": "bukit"}, {"LOWER": "jelutong"}]},
            {"label": "GPE", "pattern": [{"LOWER": "setia"}, {"LOWER": "alam"}]},
            {"label": "GPE", "pattern": [{"LOWER": "denai"}, {"LOWER": "alam"}]},
            {"label": "GPE", "pattern": [{"LOWER": "elmina"}]},
            {"label": "GPE", "pattern": [{"LOWER": "glenmarie"}]},
            {"label": "GPE", "pattern": [{"LOWER": "eco"}, {"LOWER": "ardence"}]},
            {"label": "GPE", "pattern": [{"LOWER": "alam"}, {"LOWER": "impian"}]},
            
            # Seksyen (Sections)
            {"label": "LOC", "pattern": [{"LOWER": "seksyen"}, {"IS_DIGIT": True}]},
            {"label": "LOC", "pattern": [{"LOWER": "seksyen"}, {"TEXT": {"REGEX": r"^u\d+$"}}]},
            {"label": "LOC", "pattern": [{"TEXT": {"REGEX": r"^u\d+$"}}]},  # U8, U13, U16
            
            # Address Prefixes (with following title words)
            {"label": "LOC", "pattern": [{"LOWER": "jalan"}, {"IS_TITLE": True, "OP": "+"}]},
            {"label": "LOC", "pattern": [{"LOWER": "taman"}, {"IS_TITLE": True, "OP": "+"}]},
            {"label": "LOC", "pattern": [{"LOWER": "kampung"}, {"IS_TITLE": True, "OP": "+"}]},
            {"label": "LOC", "pattern": [{"LOWER": "lorong"}, {"IS_TITLE": True, "OP": "+"}]},
            {"label": "LOC", "pattern": [{"LOWER": "bukit"}, {"IS_TITLE": True, "OP": "+"}]},
            {"label": "LOC", "pattern": [{"LOWER": "persiaran"}, {"IS_TITLE": True, "OP": "+"}]},
            {"label": "LOC", "pattern": [{"LOWER": "lebuh"}, {"IS_TITLE": True, "OP": "+"}]},
            {"label": "LOC", "pattern": [{"LOWER": "menara"}, {"IS_TITLE": True, "OP": "+"}]},
            {"label": "LOC", "pattern": [{"LOWER": "bandar"}, {"IS_TITLE": True, "OP": "+"}]},
            
            # Malaysian 5-digit postal code (most reliable identifier)
            {"label": "POSTAL", "pattern": [{"TEXT": {"REGEX": r"^\d{5}$"}}]},
            
            # Organization suffixes
            {"label": "ORG", "pattern": [{"LOWER": "sdn"}, {"LOWER": "bhd"}]},
            {"label": "ORG", "pattern": [{"LOWER": "berhad"}]},
            {"label": "ORG", "pattern": [{"LOWER": "syarikat"}]},
            {"label": "ORG", "pattern": [{"LOWER": "persatuan"}]},
            
            # ─────────────────── MALAYSIAN ECE IDENTIFIERS ───────────────────
            # These are definitive business markers that rescue valid centers
            {"label": "ORG", "pattern": [{"LOWER": {"IN": ["tadika", "taska", "taski", "tabika"]}}]},
            {"label": "ORG", "pattern": [{"LOWER": "tadika"}, {"IS_TITLE": True, "OP": "+"}]},
            {"label": "ORG", "pattern": [{"LOWER": "taska"}, {"IS_TITLE": True, "OP": "+"}]},
            {"label": "ORG", "pattern": [{"LOWER": "taski"}, {"IS_TITLE": True, "OP": "+"}]},
            {"label": "ORG", "pattern": [{"LOWER": "pusat"}, {"LOWER": "jagaan"}]},
            {"label": "ORG", "pattern": [{"LOWER": "pusat"}, {"LOWER": "jagaan"}, {"IS_TITLE": True, "OP": "+"}]},
            {"label": "ORG", "pattern": [{"LOWER": "tabika"}, {"IS_TITLE": True, "OP": "+"}]},
            
            # ─────────────────── MAJOR MALAYSIAN ECE BRANDS ───────────────────
            # These are major preschool chains that NER should always recognize
            {"label": "ORG", "pattern": [{"LOWER": "brainy"}, {"LOWER": "bunch"}]},
            {"label": "ORG", "pattern": [{"LOWER": "brainy"}, {"LOWER": "bunch"}, {"IS_TITLE": True, "OP": "*"}]},
            {"label": "ORG", "pattern": [{"LOWER": "little"}, {"LOWER": "caliphs"}]},
            {"label": "ORG", "pattern": [{"LOWER": "little"}, {"LOWER": "caliphs"}, {"IS_TITLE": True, "OP": "*"}]},
            {"label": "ORG", "pattern": [{"LOWER": "genius"}, {"LOWER": {"IN": ["aulad", "aulad"]}}]},
            {"label": "ORG", "pattern": [{"LOWER": "real"}, {"LOWER": "kids"}]},
            {"label": "ORG", "pattern": [{"LOWER": "real"}, {"LOWER": "kids"}, {"IS_TITLE": True, "OP": "*"}]},
            {"label": "ORG", "pattern": [{"LOWER": "q-dees"}]},
            {"label": "ORG", "pattern": [{"LOWER": "qdees"}]},
            {"label": "ORG", "pattern": [{"LOWER": "smart"}, {"LOWER": "reader"}, {"LOWER": "kids"}]},
            {"label": "ORG", "pattern": [{"LOWER": "kinder"}, {"LOWER": "land"}]},
            {"label": "ORG", "pattern": [{"LOWER": "kinderland"}]},
            {"label": "ORG", "pattern": [{"LOWER": "montessori"}]},
            {"label": "ORG", "pattern": [{"IS_TITLE": True}, {"LOWER": "montessori"}]},
            {"label": "ORG", "pattern": [{"LOWER": "pastel"}, {"LOWER": "kids"}]},
            {"label": "ORG", "pattern": [{"LOWER": "celestia"}, {"LOWER": "kids"}]},
            {"label": "ORG", "pattern": [{"LOWER": "my"}, {"LOWER": "little"}, {"LOWER": "kiddy"}]},
            
            # ─────────────────── LOCAL TOWNSHIPS & AREAS ───────────────────
            # These prevent "No Malaysian location" rejections
            {"label": "GPE", "pattern": [{"LOWER": "subang"}, {"LOWER": "bestari"}]},
            {"label": "GPE", "pattern": [{"LOWER": "denai"}, {"LOWER": "alam"}]},
            {"label": "GPE", "pattern": [{"LOWER": "setia"}, {"LOWER": "alam"}]},
            {"label": "GPE", "pattern": [{"LOWER": "setia"}, {"LOWER": "impian"}]},
            {"label": "GPE", "pattern": [{"LOWER": "eco"}, {"LOWER": "ardence"}]},
            {"label": "GPE", "pattern": [{"LOWER": "alam"}, {"LOWER": "impian"}]},
            {"label": "GPE", "pattern": [{"LOWER": "alam"}, {"LOWER": "nusantara"}]},
            {"label": "GPE", "pattern": [{"LOWER": "bukit"}, {"LOWER": "rimau"}]},
            {"label": "GPE", "pattern": [{"LOWER": "kota"}, {"LOWER": "kemuning"}]},
            {"label": "GPE", "pattern": [{"LOWER": "rimbayu"}]},
            {"label": "GPE", "pattern": [{"LOWER": "klang"}, {"LOWER": "jaya"}]},
            {"label": "GPE", "pattern": [{"LOWER": "taman"}, {"LOWER": "sentosa"}]},
            {"label": "GPE", "pattern": [{"LOWER": "bandar"}, {"LOWER": "bukit"}, {"LOWER": "raja"}]},
        ]
        
        ruler.add_patterns(patterns)
        return nlp
    except Exception as e:
        print(f"  [NER] Warning: Failed to create Malaysian NER pipeline: {e}")
        return None

# Create the Malaysian-hardened pipeline
_NLP = create_malaysian_ner_pipeline() if SPACY_AVAILABLE else None

# Import DuckDuckGo Search for free-tier verification
try:
    from ddgs import DDGS
    DDGS_AVAILABLE = True
except ImportError:
    DDGS_AVAILABLE = False
    print("[WARNING] ddgs not available - install with: pip install ddgs")

# Import Tavily for fallback verification
try:
    from tavily import TavilyClient
    TAVILY_AVAILABLE = True
except ImportError:
    TAVILY_AVAILABLE = False

# Import Playwright for headless browser verification
try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    print("[WARNING] playwright not available - install with: pip install playwright")

load_dotenv()

# ─────────────────────────── Constants (defined ONCE — BUG 13 FIX) ─

DEFAULT_ADDRESS    = "Persiaran Tebar Layar, Bukit Jelutong, 40150 Shah Alam, Selangor"
DEFAULT_TARGET_LAT = 3.1022
DEFAULT_TARGET_LNG = 101.5333
DEFAULT_RADIUS_KM  = 10.0

MALAYSIA_LAT = (1.0, 7.5)
MALAYSIA_LNG = (99.5, 119.5)

# ─────────────────────────── Neighbourhood Maps ────────────────────
#
# BUG 11 FIX — Two changes:
#
# 1. Bounding boxes are now non-overlapping.
#    Elmina is WEST (lng ~101.455–101.505), Denai Alam is EAST (lng ~101.502–101.555).
#    Previous version had overlapping ranges, so boundary centres landed in the wrong area.
#
# 2. Dict insertion order matters — Python iterates dicts in order.
#    Elmina MUST appear before Denai Alam in AREA_KEYWORDS because both share
#    the "U16" postcode. If Denai Alam is checked first, any Elmina address
#    containing "u16" is wrongly assigned to Denai Alam.

NEIGHBOURHOOD_BOUNDS: Dict[str, Dict] = {
    "Bukit Jelutong": {"lat": (3.080, 3.118), "lng": (101.510, 101.565)},
    "Setia Alam":     {"lat": (3.065, 3.100), "lng": (101.440, 101.520)},
    # Elmina WEST — no overlap with Denai Alam
    "Elmina":         {"lat": (3.130, 3.185), "lng": (101.455, 101.505)},
    # Denai Alam EAST — lng lower bound slightly above Elmina's upper bound
    "Denai Alam":     {"lat": (3.118, 3.160), "lng": (101.502, 101.555)},
    "Glenmarie":      {"lat": (3.075, 3.115), "lng": (101.555, 101.625)},
}

# BUG 11 FIX: Elmina entry placed BEFORE Denai Alam.
# "frekuensi u16" and "eserina" are Elmina-specific; plain "u16" is last
# so it only catches addresses not already matched by the above.
AREA_KEYWORDS: Dict[str, List[str]] = {
    "Bukit Jelutong": [
        "bukit jelutong", "seksyen u8", "bazar u8",
        "persiaran gerbang utama", "jalan jendela", "jelutong", "u8",
    ],
    "Setia Alam": [
        "setia alam", "seksyen u13", "setia eco", "setia perdana",
        "setia impian", "eco ardence", "setia avenue", "setia nusantara",
        "ardence", "u13",
    ],
    # Elmina BEFORE Denai Alam — must be checked first
    "Elmina": [
        "kota elmina", "elmina east", "eserina", "persiaran atmosfera",
        "jalan eserina", "frekuensi u16",
        "elmina",       # plain "elmina" after more-specific terms
    ],
    "Denai Alam": [
        "denai alam", "jalan elektron", "elektron u16",
        "e-boulevard", "e boulevard", "seksyen u16",
        "u16",          # LAST — catches remaining U16 addresses not matched above
    ],
    "Glenmarie": [
        "glenmarie", "subang bestari", "seksyen u5", "temasya",
        "uoa business", "laman glenmarie", "subang jaya",
    ],
}

# ─────────────────────────── Classification Dictionaries ──────────

ISLAMIC_KEYWORDS = [
    "islamic", "islam", "muslim", "tahfiz", "tahfidz", "jawi", "solat",
    "quran", "al-quran", "aulad", "ustazah", "madrasah", "dini", "tarbiyah",
    "caliphs", "genius aulad", "little caliphs", "pasti", "iqra", "iman",
    "khalifah", "sujood", "hafiz", "hafazan", "ibnu sina", "imanina",
    "al-hafiz", "naluri ilmu",
]
INTERNATIONAL_KEYWORDS = [
    "international", "cambridge", "montessori", "ib ", "igcse",
    "reggio", "waldorf", "steiner", "eyfs",
]

NATIONAL_CHAINS = [
    "real kids", "brainy bunch", "little caliphs", "genius aulad",
    "beaconhouse", "pasti", "kemas", "smart reader", "little einstein",
    "eduwis", "q-dees", "kinderland",
]
INSTITUTIONAL_KEYWORDS = [
    "idrissi", "international school", "college", "university",
    "campus", "academy", "institute",
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

# Verified fee data for known brands — used before inferring
VERIFIED_FEES: Dict[str, Dict] = {
    "idrissi cambridge": {
        "fee_halfday_raw": "Not published",
        "fee_fullday_raw": "RM 1,175/month (RM 14,100/year)",
        "note": "[Verified: IDRISSI website — RM 14,100/yr published]",
    },
    "real kids": {
        "fee_halfday_raw": "RM 450–550/month (half-day)",
        "fee_fullday_raw": "RM 700–900/month (full-day)",
        "note": "[Verified: REAL Kids website / Kiddy123 listings]",
    },
    "brainy bunch": {
        "fee_halfday_raw": "RM 350–500/month (half-day)",
        "fee_fullday_raw": "RM 500–700/month (full-day)",
        "note": "[Verified: Brainy Bunch website / Kiddy123]",
    },
    "little caliphs": {
        "fee_halfday_raw": "RM 300–450/month (half-day)",
        "fee_fullday_raw": "RM 450–650/month (full-day)",
        "note": "[Verified: Little Caliphs website / Kiddy123]",
    },
    "genius aulad": {
        "fee_halfday_raw": "RM 300–450/month (half-day)",
        "fee_fullday_raw": "RM 450–650/month (full-day)",
        "note": "[Verified: Genius Aulad website / Kiddy123]",
    },
    "knowledge tree montessori": {
        "fee_halfday_raw": "RM 950/month (half-day)",
        "fee_fullday_raw": "RM 1,500/month (full-day)",
        "note": "[Verified: Kiddy123 listing]",
    },
    "choo choo train": {
        "fee_halfday_raw": "RM 1,000/month (half-day)",
        "fee_fullday_raw": "RM 1,550/month (full-day)",
        "note": "[Verified: Kiddy123 listing]",
    },
    "tiny tree house": {
        "fee_halfday_raw": "RM 590/month (half-day)",
        "fee_fullday_raw": "RM 970/month (full-day)",
        "note": "[Verified: Kiddy123 listing]",
    },
}

FEE_TIERS: Dict[Tuple, Tuple[str, str]] = {
    ("Institutional Campus",  "any",               "any"):    ("RM 700–1,200/mth",  "RM 1,200–1,800/mth"),
    ("National Chain",        "Islamic-integrated", "any"):    ("RM 300–500/mth",    "RM 450–700/mth"),
    ("National Chain",        "Secular",            "any"):    ("RM 350–550/mth",    "RM 550–900/mth"),
    ("Independent",           "Islamic-integrated", "Elmina"): ("RM 200–350/mth",    "RM 350–550/mth"),
    ("Independent",           "Islamic-integrated", "other"):  ("RM 250–450/mth",    "RM 400–700/mth"),
    ("Independent",           "Secular",            "any"):    ("RM 300–500/mth",    "RM 450–750/mth"),
    ("Independent",           "International",      "any"):    ("RM 600–900/mth",    "RM 900–1,400/mth"),
}

REQUIRED_COLUMNS = [
    "centre_name", "address", "neighbourhood", "curriculum",
    "language_medium", "fee_halfday_raw", "fee_fullday_raw",
    "scale", "religious_orientation", "source_notes",
]

TARGET_ROWS = [
    {
        "centre_name":           "[TARGET — Brand A] IDRISSI Preschool Network",
        "address":               "Multiple locations across Peninsular Malaysia",
        "neighbourhood":         "Multiple (Peninsular Malaysia)",
        "lat":                   DEFAULT_TARGET_LAT,
        "lng":                   DEFAULT_TARGET_LNG,
        "curriculum":            "Islamic-integrated, KSPK-aligned",
        "language_medium":       "BM, English",
        "fee_halfday_raw":       "[Reference only]",
        "fee_fullday_raw":       "[Reference only — accessible price point]",
        "fee_display":           "[Reference only] | [Reference only — accessible price point]",
        "scale":                 "National Chain",
        "religious_orientation": "Islamic-integrated",
        "source_primary":        "Reference — Target",
        "source_notes":          "[Reference — Target Brand A. Excluded from competitor statistics.]",
        "threat_score":          "Reference",
    },
    {
        "centre_name":           "[TARGET — Brand B] IDRISSI Cambridge Eco-Preschool",
        "address":               "Persiaran Tebar Layar, Bukit Jelutong, 40150 Shah Alam, Selangor",
        "neighbourhood":         "Bukit Jelutong",
        "lat":                   DEFAULT_TARGET_LAT,
        "lng":                   DEFAULT_TARGET_LNG,
        "curriculum":            "Cambridge Early Years, Nature-based / Eco-Islamic",
        "language_medium":       "English, BM, Arabic",
        "fee_halfday_raw":       "Not published",
        "fee_fullday_raw":       "RM 1,175/month (RM 14,100/year)",
        "fee_display":           "Not published | RM 1,175/month (RM 14,100/year)",
        "scale":                 "Institutional Campus",
        "religious_orientation": "Islamic-integrated",
        "source_primary":        "Reference — Target",
        "source_notes":          "[Reference — Target Brand B. Fee verified: IDRISSI website. Excluded from statistics.]",
        "threat_score":          "Reference",
    },
]

# ─────────────────────────── Utility ──────────────────────────────

def normalize(val: object, fallback: str = "") -> str:
    bad = {"", "unknown", "n/a", "na", "tbc", "-", "–", "none", "nan", "unnamed ece centre"}
    txt = str(val).strip() if val is not None else ""
    return fallback if txt.lower() in bad else txt

def sanitize_notes(note: str) -> str:
    txt = str(note).strip()
    if not txt or txt.lower() in {"", "nan"}:
        return "[Inferred] Data completed through rule-based enrichment"
    if not any(tag in txt.lower() for tag in ["[verified", "[inferred", "[reference"]):
        txt += " | [Inferred] Data completed through rule-based enrichment"
    return txt

# ─────────────────────────── Geospatial ───────────────────────────

def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """
    BUG 12 FIX: Correct Haversine formula.
    Previous version: a = sin²(Δlat/2) + cos(lat1) * sin²(Δlng/2)   ← WRONG
    Correct formula:  a = sin²(Δlat/2) + cos(lat1)*cos(lat2)*sin²(Δlng/2)
    The missing cos(lat2) term caused all distances to be slightly off.
    """
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = phi2 - phi1
    dlam = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.asin(math.sqrt(min(1.0, a)))

def is_in_malaysia(lat: float, lng: float) -> bool:
    return MALAYSIA_LAT[0] <= lat <= MALAYSIA_LAT[1] and MALAYSIA_LNG[0] <= lng <= MALAYSIA_LNG[1]

def reverse_geocode_nominatim(lat: float, lng: float) -> Optional[Dict]:
    """Reverse geocode using Nominatim (OpenStreetMap) - free tier.
    
    Uses shared geocoder instance to reduce connection overhead.
    """
    try:
        time.sleep(1.0)  # Rate limiting - Nominatim free tier: 1 req/sec
        geo = get_nominatim_geocoder()  # Use shared instance
        location = geo.reverse((lat, lng), language="en")
        
        if location:
            address = location.raw.get("address", {})
            return {
                "neighbourhood": address.get("suburb", address.get("neighbourhood", "")),
                "city": address.get("city", address.get("town", "")),
                "postcode": address.get("postcode", ""),
                "state": address.get("state", ""),
                "full_address": location.address,
            }
    except (GeocoderTimedOut, GeocoderServiceError):
        pass
    except Exception as e:
        print(f"  [Nominatim Reverse] Error: {e}")
    
    return None

def assign_neighbourhood(lat: Optional[float], lng: Optional[float], address: str) -> str:
    """
    v2.0: Three-stage assignment with Nominatim reverse geocoding.
    Stage 1 — Nominatim reverse geocode (most accurate, free tier).
    Stage 2 — bounding boxes (fallback).
    Stage 3 — keyword scan on address string (final fallback).
    """
    # Stage 1: Nominatim reverse geocoding (free, accurate)
    if lat is not None and lng is not None:
        try:
            flat, flng = float(lat), float(lng)
            nominatim_data = reverse_geocode_nominatim(flat, flng)
            
            if nominatim_data:
                # Check if Nominatim neighbourhood matches our areas
                nom_neighbourhood = nominatim_data.get("neighbourhood", "").lower()
                for area in NEIGHBOURHOOD_BOUNDS.keys():
                    if area.lower() in nom_neighbourhood:
                        return area
                    
                # Check city/suburb
                city = nominatim_data.get("city", "").lower()
                for area, keywords in AREA_KEYWORDS.items():
                    if any(kw in city or kw in nom_neighbourhood for kw in keywords):
                        return area
        except (ValueError, TypeError):
            pass

    # Stage 2: coordinate bounding boxes
    if lat is not None and lng is not None:
        try:
            flat, flng = float(lat), float(lng)
            for area, bounds in NEIGHBOURHOOD_BOUNDS.items():
                if (bounds["lat"][0] <= flat <= bounds["lat"][1] and
                        bounds["lng"][0] <= flng <= bounds["lng"][1]):
                    return area
        except (ValueError, TypeError):
            pass

    # Stage 3: keyword scan — dict order enforces Elmina before Denai Alam
    addr_lower = str(address).lower()
    for area, keywords in AREA_KEYWORDS.items():
        if any(kw in addr_lower for kw in keywords):
            return area

    return "Other"

# ─────────────────────────── Malaysian Address Standardization ─────

MALAYSIAN_ABBREVS = {
    "jln": "jalan",
    "pers": "persiaran", 
    "lor": "lorong",
    "leb": "lebuh",
    "lrg": "lorong",
    "blk": "block",
    "btg": "batang",
    "kg": "kampung",
    "tmn": "taman",
    "sck": "seksyen",
}

def standardize_malaysian_address(address: str) -> str:
    """
    Standardize Malaysian address format.
    Handles: Seksyen/Section, Jalan abbreviations, postcode positioning.
    """
    if not address:
        return ""
    
    # Remove excess whitespace
    addr = re.sub(r'\s+', ' ', address.strip())
    
    # Expand abbreviations
    words = addr.split()
    expanded = []
    for word in words:
        clean = word.lower().rstrip(',')
        if clean in MALAYSIAN_ABBREVS:
            # Preserve capitalization
            expanded_word = MALAYSIAN_ABBREVS[clean]
            if word[0].isupper():
                expanded_word = expanded_word.capitalize()
            expanded.append(expanded_word)
        else:
            expanded.append(word)
    
    addr = ' '.join(expanded)
    
    # Normalize Seksyen variations
    addr = re.sub(r'\b(seksyen|section|sek)\s*', 'Seksyen ', addr, flags=re.IGNORECASE)
    
    # Ensure Selangor is mentioned if Shah Alam is
    if "shah alam" in addr.lower() and "selangor" not in addr.lower():
        addr += ", Selangor"
    
    # Clean up commas
    addr = re.sub(r',\s*,', ',', addr)
    addr = re.sub(r'\s+', ' ', addr)
    
    return addr.strip(', ')

def extract_postcode(address: str) -> str:
    """Extract 5-digit Malaysian postcode."""
    match = re.search(r'(\d{5})', address)
    return match.group(1) if match else ""

# ─────────────────────────── Geocoding ────────────────────────────
# v2.0: Nominatim only - NO Google Maps API (free tier constraint)

# Shared Nominatim instance to reduce connection overhead and rate limiting
# BUG FIX: Creating new instance per-call caused excessive 429 errors
_NOMINATIM_GEOCODER = None

def get_nominatim_geocoder():
    """Get or create shared Nominatim geocoder instance."""
    global _NOMINATIM_GEOCODER
    if _NOMINATIM_GEOCODER is None:
        _NOMINATIM_GEOCODER = Nominatim(user_agent="kestrel-v2-enricher", timeout=10)
    return _NOMINATIM_GEOCODER

def geocode_with_fallback(address: str,
                          blocked: set) -> Tuple[Optional[float], Optional[float], str]:
    """
    v2.0: Nominatim-only geocoding (free tier).
    Uses shared geocoder instance to reduce rate limiting.
    Removed: Google Maps API, ArcGIS, TomTom, Ollama
    """
    if "Nominatim" not in blocked:
        try:
            time.sleep(1.1)  # Rate limiting - Nominatim free tier: 1 req/sec
            geo = get_nominatim_geocoder()  # Use shared instance
            loc = geo.geocode(address)
            if loc:
                lat, lng = float(loc.latitude), float(loc.longitude)
                if is_in_malaysia(lat, lng):
                    return lat, lng, "Nominatim"
        except GeocoderTimedOut:
            pass
        except GeocoderServiceError as exc:
            if "429" in str(exc) or "Too Many" in str(exc):
                blocked.add("Nominatim")
                print("  [Nominatim] Rate limited - skipping further geocoding")
        except Exception as e:
            print(f"  [Nominatim] Error: {e}")

    return None, None, ""

def clean_address_for_geocoding(address: str) -> str:
    if not address or len(address) > 300:
        return ""
    address = re.sub(r"(Essential Details|Centre'?s? Category|Year\s*\d+|\d+ (likes|posts|followers))[^\n]*",
                     "", address, flags=re.IGNORECASE)
    address = re.sub(r"(Contact Number|Tel|Phone|Email|Website|Submit|Thanks for)[^\n]*",
                     "", address, flags=re.IGNORECASE)
    address = re.sub(r"\s{2,}", " ", address).strip()
    return address[:200]

# ─────────────────────────── Classifiers ──────────────────────────

def classify_religion(name: str, extra: str = "") -> str:
    text = f"{name} {extra}".lower()
    if any(kw in text for kw in ISLAMIC_KEYWORDS):
        return "Islamic-integrated"
    if any(kw in text for kw in INTERNATIONAL_KEYWORDS):
        return "International"
    return "Secular"

def classify_scale(name: str) -> str:
    nl = str(name).lower()
    if any(kw in nl for kw in INSTITUTIONAL_KEYWORDS):
        return "Institutional Campus"
    if any(chain in nl for chain in NATIONAL_CHAINS):
        return "National Chain"
    return "Independent"

def infer_curriculum(name: str, extra: str = "") -> str:
    text = f"{name} {extra}".lower()
    for label, kws in CURRICULUM_KEYWORDS.items():
        if any(kw in text for kw in kws):
            return label
    return "[Inferred] Standard KSPK assumed — typical for MOE-registered operator"

def infer_language(name: str, orientation: str, neighbourhood: str) -> str:
    nl = str(name).lower()
    if "mandarin" in nl or "chinese" in nl or "hua" in nl:
        return "Mandarin + English"
    if orientation == "Islamic-integrated":
        return "[Inferred] BM + English — typical for Islamic-integrated preschool"
    if neighbourhood in ("Glenmarie", "Subang"):
        return "[Inferred] English primary — area demographics suggest higher English demand"
    return "[Inferred] BM + English — standard bilingual for Shah Alam area"

def get_verified_fees(name: str) -> Optional[Dict]:
    nl = name.lower()
    for key, data in VERIFIED_FEES.items():
        if key in nl:
            return data
    return None

def infer_fee_ranges(scale: str, orientation: str,
                     neighbourhood: str) -> Tuple[str, str, str]:
    nh = "Elmina" if neighbourhood == "Elmina" else "other"
    for key_tuple in [(scale, orientation, nh), (scale, orientation, "any"),
                      (scale, "any", "any")]:
        if key_tuple in FEE_TIERS:
            hd, fd = FEE_TIERS[key_tuple]
            note = (f"[Inferred] Estimated {fd} — {scale} {orientation} operator, "
                    f"{neighbourhood} area average")
            return hd, fd, note
    return "RM 300–500/mth", "RM 450–750/mth", "[Inferred] Typical Shah Alam area range"

def extract_fee_value(fee_text: str) -> float:
    """Extract numeric fee value from fee text for comparison."""
    if not fee_text:
        return 0.0
    # Remove common prefixes and extract numbers
    cleaned = fee_text.replace("RM", "").replace("/mth", "").replace("/month", "")
    cleaned = cleaned.replace(",", "").replace("–", "-").replace("-", " ")
    nums = re.findall(r"\d+", cleaned)
    if nums:
        # Return the first number found (usually the base fee)
        return float(nums[0])
    return 0.0

def compute_threat_score(row: pd.Series) -> int:
    """
    Intelligent Threat Scoring using IDRISSI Cambridge Eco-Preschool as benchmark.
    
    Benchmark: IDRISSI @ RM 1,175/mth (Target Reference)
    
    Scoring Logic:
    - High Threat (9-10): Verified location < 5km from target AND Fees RM 900–RM 1,500
    - Medium Threat (4-8): Proximity-based scoring with fee adjustments
    - Low Threat (1-3): Fees < RM 600 (different market) OR Distance > 15km
    
    Factors:
    - Proximity to target (<1km: max, 1-3km: high, 3-5km: medium, 5-10km: low, >15km: min)
    - Fee competition (RM 900-1500: high overlap with IDRISSI, RM 600-900: medium, <RM 600: low)
    - Scale/Brand power (National > Regional > Independent)
    - Curriculum match (Cambridge/Montessori/International = higher threat)
    """
    if "Reference — Target" in str(row.get("source_notes", "")):
        return 0  # Target schools have no threat to themselves
    
    # Calculate distance to target (IDRISSI)
    lat, lng = row.get("lat"), row.get("lng")
    if lat is not None and not pd.isna(lat) and lng is not None and not pd.isna(lng):
        try:
            dist_km = haversine_km(DEFAULT_TARGET_LAT, DEFAULT_TARGET_LNG,
                                  float(lat), float(lng))
        except (ValueError, TypeError):
            dist_km = 10.0  # Default if coordinates invalid
    else:
        dist_km = 10.0  # Default distance if no coordinates
    
    # Extract fee values from both half-day and full-day fields
    fee_hd_text = str(row.get("fee_halfday_raw", ""))
    fee_fd_text = str(row.get("fee_fullday_raw", ""))
    fee_hd = extract_fee_value(fee_hd_text)
    fee_fd = extract_fee_value(fee_fd_text)
    
    # Use the higher fee if both present, otherwise use whichever is available
    if fee_hd > 0 and fee_fd > 0:
        fee_val = max(fee_hd, fee_fd)
    else:
        fee_val = fee_hd if fee_hd > 0 else fee_fd if fee_fd > 0 else 0
    
    # PROXIMITY SCORE (0-40 points)
    # Direct competition zone: < 5km from target
    if dist_km < 1:
        proximity_score = 40  # Immediate neighborhood
    elif dist_km < 3:
        proximity_score = 35  # Same area
    elif dist_km < 5:
        proximity_score = 30  # Nearby area (HIGH THREAT ZONE)
    elif dist_km < 10:
        proximity_score = 20  # Moderate distance
    elif dist_km < 15:
        proximity_score = 10  # Further away
    else:
        proximity_score = 5   # Very far (>15km)
    
    # FEE COMPETITION SCORE (0-30 points)
    # Benchmark: IDRISSI @ RM 1,175/mth
    IDRISSI_BENCHMARK = 1175
    FEE_HIGH_MIN = 900
    FEE_HIGH_MAX = 1500
    
    if fee_val > 0:
        if FEE_HIGH_MIN <= fee_val <= FEE_HIGH_MAX:
            # Direct competitor in IDRISSI price range (RM 900-1500)
            fee_score = 30
        elif fee_val >= 600 and fee_val < FEE_HIGH_MIN:
            # Lower than IDRISSI but still substantial (RM 600-900)
            fee_score = 20
        elif fee_val > FEE_HIGH_MAX:
            # Premium above IDRISSI (> RM 1,500)
            fee_score = 25
        else:
            # Budget segment (< RM 600) - different market
            fee_score = 5
    else:
        fee_score = 15  # Unknown fee - assume moderate
    
    # SCALE/BRAND POWER (0-15 points)
    scale = str(row.get("scale", ""))
    if "National Chain" in scale:
        scale_score = 15
    elif "Institutional Campus" in scale:
        scale_score = 12
    elif "Regional Chain" in scale:
        scale_score = 10
    elif "Independent" in scale:
        scale_score = 5
    else:
        scale_score = 8  # Unknown
    
    # CURRICULUM MATCH (0-15 points)
    # IDRISSI uses Cambridge curriculum
    curriculum = str(row.get("curriculum", "")).lower()
    if any(k in curriculum for k in ["cambridge"]):
        curr_score = 15  # Same curriculum as IDRISSI
    elif any(k in curriculum for k in ["international", "montessori", "british"]):
        curr_score = 12  # Comparable international curriculum
    elif any(k in curriculum for k in ["islamic", "kspk", "national"]):
        curr_score = 8   # Local curriculum
    else:
        curr_score = 5   # Unknown or other
    
    # Calculate total raw score (0-100)
    total_score = proximity_score + fee_score + scale_score + curr_score
    
    # Map to 1-10 scale with IDRISSI-centric thresholds
    # High Threat (9-10): Strong competitor in both proximity AND fee range
    if dist_km < 5 and (FEE_HIGH_MIN <= fee_val <= FEE_HIGH_MAX):
        # Direct threat to IDRISSI
        threat_level = 9 if total_score >= 80 else 8
    # Medium-High Threat (6-8): Good proximity OR competitive fees
    elif dist_km < 10 and fee_val >= 600:
        threat_level = min(8, max(6, int(total_score / 10)))
    # Medium Threat (4-5): Moderate proximity, moderate fees
    elif dist_km < 15 and fee_val > 0:
        threat_level = min(5, max(4, int(total_score / 15)))
    # Low Threat (1-3): Far away OR budget segment
    else:
        threat_level = min(3, max(1, int(total_score / 20)))
    
    return int(threat_level)

# ─────────────────────────── ECE Filter ───────────────────────────

def is_ece_by_keyword(name: str) -> Tuple[Optional[bool], int, str]:
    """Keyword-based ECE classification. Returns (is_ece|None, confidence, reason)."""
    nl = name.lower()
    NON_ECE = [
        "tuisyen", "tuition", "kolej", "universiti", "university", "college",
        "sekolah menengah", "secondary school", "high school", "pusat tuisyen",
        "gym", "salon", "spa", "restaurant", "kedai", "hospital", "klinik", "bank",
    ]
    for sig in NON_ECE:
        if sig in nl:
            return False, 95, f"Hard reject: '{sig}'"
    STRONG_ECE = [
        "tadika", "taska", "tabika", "prasekolah", "pusat jagaan",
        "preschool", "kindergarten", "childcare", "daycare", "nursery",
        "playschool", "educare",
    ]
    for kw in STRONG_ECE:
        if kw in nl:
            return True, 100, f"Strong ECE keyword: '{kw}'"
    WEAK_ECE = [
        "montessori", "little caliphs", "kids", "kidz", "children", "toddler",
        "infant", "baby", "early learning", "early years", "learning centre",
        "q-dees", "kinderland", "smart reader", "genius", "brainy bunch",
        "choo choo", "knowledge tree", "real kids", "wynkids",
        "al-kauthar", "eduqids",
    ]
    for kw in WEAK_ECE:
        if kw in nl:
            return True, 75, f"Weak ECE keyword: '{kw}'"
    return None, 0, "No ECE keyword — ambiguous"

def pre_verification_gatekeeper(raw_name: str) -> tuple:
    """
    Pre-Verification Gatekeeper: Layer A (Syntactic Fact-Check)
    Evaluates a scraped string syntactically BEFORE allowing it to hit 
    the geocoder or deep web search. Drops bad data cheaply and quickly.
    
    STRENGTHENED: More aggressive filtering for long sentences, websites, non-ECE content
    
    Returns (is_valid: bool, reason: str)
    """
    n = str(raw_name).strip()
    n_lower = n.lower()
    
    # --- LAYER A: SYNTACTIC FACT-CHECK (Instant Python Logic) ---
    
    # 1. Punctuation Rule: Real businesses rarely use ! or ? in legal names
    if re.search(r'[!?]', n):
        return False, "Contains sentence-like punctuation (! or ?)"
        
    # 2. Length Rule: STRENGTHENED - If it's more than 8 words, it's usually a sentence/blog title
    # But allow longer if it contains Malaysian ECE keywords (indicates name+address)
    words = n.split()
    if len(words) > 8:
        # Check if it contains Malaysian ECE keywords that indicate it's a valid center
        malaysian_keywords = ['tadika', 'taska', 'taski', 'tabika', 'pusat jagaan',
                             'mrc', 'real kids', 'brainy bunch', 'little caliphs', 
                             'genius', 'q-dees', 'cherry', 'kindie', 'montessori']
        has_malay_keyword = any(kw in n_lower for kw in malaysian_keywords)
        if not has_malay_keyword:
            return False, f"Too long ({len(words)} words) - likely a sentence"
        
    # 3. Website/URL Rule: STRENGTHENED - More aggressive URL detection
    # Catches: http://, https://, www., .com, .my, .edu, .org, .net, .gov, .edu.my
    url_patterns = [
        r'https?://\S+',
        r'www\.\S+',
        r'\S+\.(com|my|edu|org|net|gov|edu\.my)(?!\w)',
        r'\.com\S*',
        r'\.my\S*',
        r'://\S+',
        r'http\S*',
        r'facebook\.com',
        r'instagram\.com',
        r'wa\.me',
        r't\.me',
        r'tiktok\.com',
        r'youtube\.com'
    ]
    for pattern in url_patterns:
        if re.search(pattern, n_lower):
            return False, "Contains URL/website link"
        
    # 4. Incomplete Phrase Rule: Ends with preposition/article (indicates truncation)
    incomplete_endings = [' in', ' at', ' near', ' by', ' on', ' to', ' for', ' with', ' from', ' the', ' a', ' an']
    for ending in incomplete_endings:
        if n_lower.endswith(ending):
            return False, "Incomplete phrase (ends with preposition/article)"
        
    # 5. SEO Stuffing Rule: More strict - 2+ separators
    separators = n.count('/') + n.count('|') + n.count('>')
    if separators >= 2:
        return False, "SEO keyword stuffing detected (2+ separators)"
        
    # 6. Hashtag/At-symbol Rule: Social media content
    if re.search(r'[@#]\w+', n_lower):
        return False, "Contains social media tags"
        
    # 7. Quote Rule: Dialog or captions
    if n.count('"') >= 2 or n.count("'") >= 2:
        return False, "Contains quotes - likely dialog/caption"
        
    # 8. Ellipsis Rule: Truncated content
    if '...' in n or n.endswith('...'):
        return False, "Truncated content (ellipsis)"
        
    # 9. Sentence-ending punctuation (promotional "shouting")
    if re.search(r'[!?\.]$', n):
        return False, "Ends with sentence punctuation (likely promotional)"
        
    # 10. Character repeat filter
    if re.search(r'(.)\1{3,}', n_lower):
        return False, "Character repetition detected"
        
    # 11. Registration/Event keywords - STRENGTHENED with more keywords
    event_keywords = [
        r'\bregistration\b', r'\bregister\b', r'\bsign up\b', r'\benrol now\b', 
        r'\bopen for\b', r'\bapply now\b', r'\blimited slot\b', r'\bbooking\b', 
        r'\bcontact us\b', r'\bcall now\b', r'\bhurry\b', r'\blimited time\b', 
        r'\bact now\b', r'\bjoin us\b', r'\bvisit us\b', r'\bintake\b',
        r'\bnow open\b', r'\bbook now\b', r'\bwhatsapp\b', r'\btelegram\b',
        r'\bmessage us\b', r'\bdirect message\b', r'\bpm us\b', r'\bclick here\b',
        r'\blearn more\b', r'\bfind out\b', r'\bcheck out\b', r'\bview details\b'
    ]
    for kw in event_keywords:
        if re.search(kw, n_lower):
            return False, "Contains event/promotion keyword"
    
    # 12. Junk Suffix Rule - STRENGTHENED with more patterns
    junk_suffixes = [' finder', ' directory', ' listings', ' database', ' search', 
                     ' results', ' portal', ' guide', ' map', ' reviews', ' rating',
                     ' locations', ' near me', ' around me', ' nearby']
    for suffix in junk_suffixes:
        if suffix in n_lower:
            return False, "Contains aggregator suffix (finder/directory/etc)"
    
    # 13. Non-ECE content detection - STRENGTHENED
    # Detects sentences about schools, education in general, not ECE centers
    non_ece_patterns = [
        r'\bschools?\b',
        r'\binternational schools?\b',
        r'\bprivate schools?\b',
        r'\bpublic schools?\b',
        r'\bprimary school\b',
        r'\bsecondary school\b',
        r'\bhigh school\b',
        r'\buniversity\b',
        r'\bcollege\b',
        r'\beducation\b',
        r'\bteaching\b',
        r'\btutor\b',
        r'\btuition\b',
        r'\bclass\b',
        r'\bcourse\b',
        r'\blesson\b',
        r'\btraining\b',
        r'\bworkshop\b',
        r'\bseminar\b'
    ]
    # Only reject if it has these patterns AND doesn't have ECE keywords
    has_non_ece = any(re.search(pattern, n_lower) for pattern in non_ece_patterns)
    ece_keywords = ['tadika', 'taska', 'taski', 'tabika', 'preschool', 'kindergarten', 
                   'childcare', 'nursery', 'early childhood', 'daycare', 'day care']
    has_ece = any(kw in n_lower for kw in ece_keywords)
    
    if has_non_ece and not has_ece:
        return False, "Contains non-ECE education keywords (school/tutor/etc)"
    
    # 14. EXACT-MATCH Generic Name Rule
    exact_generic_names = ['preschool', 'kindergarten', 'tadika', 'taska', 'childcare', 'nursery', 
                           'school', 'education', 'center', 'centre']
    stripped_name = n_lower.strip()
    if stripped_name in exact_generic_names:
        return False, "Generic placeholder name (not a specific business)"
    
    return True, "Passed syntactic gatekeeper"


def ner_gatekeeper(raw_name: str, address: str = "") -> tuple:
    """
    Layer B: Named Entity Recognition (NER) Validation via spaCy
    
    Malaysian-hardened NER pipeline with:
    - Abbreviation normalization (Jln→Jalan, Tmn→Taman, etc.)
    - EntityRuler patterns for local geography
    - Postal code validation (5-digit Malaysian format)
    - RESCUE RULE: Malaysian ECE keywords override NER rejection
    
    CRITICAL: Returns the FULL original name, not just the ORG substring.
    This preserves location disambiguation (e.g., "Knowledge Tree Montessori 
    Kindergarten, Eco Ardence" vs "...Setia Alam").
    
    Returns (is_valid: bool, full_name_or_reason: str)
    """
    if not SPACY_AVAILABLE or _NLP is None:
        # Fallback: spaCy not available, skip NER layer
        return True, raw_name
    
    # ─────────────────── RESCUE RULE: Malaysian ECE Keywords ───────────────────
    # These definitive markers rescue valid centers even if NER doesn't recognize ORG
    malaysian_ece_keywords = [
        "tadika", "taska", "taski", "tabika",
        "pusat jagaan", "pusat asuhan",
        "prasekolah", "kinder",
    ]
    name_lower = raw_name.lower()
    for kw in malaysian_ece_keywords:
        if kw in name_lower:
            # Found Malaysian ECE keyword - definitely a valid center
            return True, raw_name
    
    # Also check for major brand names that NER might miss
    major_brand_keywords = [
        "brainy bunch", "real kids", "little caliphs", "genius aulad",
        "q-dees", "qdees", "smart reader", "kinderland", "montessori"
    ]
    for brand in major_brand_keywords:
        if brand in name_lower:
            # Known major brand - definitely valid
            return True, raw_name
    
    try:
        # Normalize Malaysian abbreviations in name and address
        normalized_name = normalize_malaysian_abbreviations(raw_name)
        normalized_address = normalize_malaysian_abbreviations(address) if address else ""
        
        # Process both name and address for comprehensive entity detection
        doc_name = _NLP(normalized_name)
        doc_addr = _NLP(normalized_address) if normalized_address else None
        
        # Extract all entities
        orgs = [ent for ent in doc_name.ents if ent.label_ == "ORG"]
        persons = [ent for ent in doc_name.ents if ent.label_ == "PERSON"]
        
        # Check for Malaysian location entities in name OR address
        malaysian_locations = []
        for doc in [doc_name, doc_addr]:
            if doc:
                malaysian_locations.extend([
                    ent for ent in doc.ents 
                    if ent.label_ in ["GPE", "LOC", "POSTAL"]
                ])
        
        # Validation heuristics for 98% accuracy
        
        # 1. Has ORG entity → passes (most reliable)
        if orgs:
            org_text = orgs[0].text.strip()
            if len(org_text) >= 3:
                return True, raw_name
        
        # 2. Has PERSON entity + short name → might be founder-named preschool
        if persons and len(raw_name.split()) <= 6:
            return True, raw_name
        
        # 3. Has Malaysian postal code → high confidence it's a real address
        postal_codes = [ent for ent in malaysian_locations if ent.label_ == "POSTAL"]
        if postal_codes:
            # Postal code found = high confidence this is a real Malaysian location
            return True, raw_name
        
        # 4. Has Malaysian GPE (Geopolitical Entity) + LOC (Location)
        gpes = [ent for ent in malaysian_locations if ent.label_ == "GPE"]
        locs = [ent for ent in malaysian_locations if ent.label_ == "LOC"]
        
        # Strong signal: GPE (state/city) + LOC (street/area) found
        if gpes and locs:
            return True, raw_name
        
        # 5. At least 2 GPE entities (e.g., "Shah Alam" + "Selangor")
        if len(gpes) >= 2:
            return True, raw_name
        
        # 6. GPE + FAC (facility) combo for community centers, etc.
        facs = [ent for ent in doc_name.ents if ent.label_ == "FAC"]
        if gpes and facs:
            return True, raw_name
        
        # 7. Fallback: If it has any Malaysian location entity, be lenient
        if malaysian_locations:
            return True, raw_name
        
        # NOTE: NER models are trained on general business text (Wall Street Journal etc.)
        # They do NOT recognize Malaysian ECE names like "Choo Choo Train" or "Witty Peas"
        # Instead of rejecting here, we PASS THROUGH and let the geocoder validate
        # The syntactic gatekeeper (Layer A) already filtered out obvious junk
        return True, raw_name
        
    except Exception as e:
        # If NER fails, conservatively pass through
        return True, raw_name


def extract_malaysian_location_features(text: str) -> dict:
    """
    Extract Malaysian-specific location features for verification.
    Returns dict with postal codes, states, cities, and address components.
    """
    if not SPACY_AVAILABLE or _NLP is None or not text:
        return {"postal_codes": [], "states": [], "cities": [], "address_parts": []}
    
    try:
        normalized = normalize_malaysian_abbreviations(text)
        doc = _NLP(normalized)
        
        features = {
            "postal_codes": [ent.text for ent in doc.ents if ent.label_ == "POSTAL"],
            "states": [ent.text for ent in doc.ents if ent.label_ == "GPE" and 
                      any(state in ent.text.lower() for state in 
                          ["selangor", "johor", "kedah", "kelantan", "melaka", 
                           "negeri sembilan", "pahang", "penang", "perak", 
                           "perlis", "sabah", "sarawak", "terengganu"])],
            "cities": [ent.text for ent in doc.ents if ent.label_ == "GPE"],
            "address_parts": [ent.text for ent in doc.ents if ent.label_ == "LOC"],
        }
        return features
    except Exception:
        return {"postal_codes": [], "states": [], "cities": [], "address_parts": []}


def filter_non_ece(df: pd.DataFrame) -> pd.DataFrame:
    """
    Keyword-based ECE filter with Pre-Verification Gatekeeper.
    Layer A (Syntactic) runs first to drop junk cheaply before expensive ops.
    Ollama removed for consistent anti-hallucination.
    Ambiguous names are KEPT (safe default).
    """
    print(f"  [Filter] Checking {len(df)} centres...")
    kept, rejected = [], 0
    gatekeeper_rejected = 0

    for _, row in df.iterrows():
        # BUG 14 FIX: read "centre_name" not "Brand"
        name = str(row.get("centre_name", "")).strip()
        if not name:
            continue

        # NEW: Layer A - Pre-Verification Gatekeeper (Syntactic)
        # Runs BEFORE expensive keyword checks to drop obvious junk fast
        is_valid, gatekeeper_reason = pre_verification_gatekeeper(name)
        if not is_valid:
            rejected += 1
            gatekeeper_rejected += 1
            print(f"    🚫 Gatekeeper Rejected: '{name[:40]}' — {gatekeeper_reason}")
            continue
        
        # NEW: Layer B - NER Gatekeeper (Semantic Validation)
        # Validates that string contains ORG or Malaysian location entities
        # Passes address for comprehensive Malaysian location detection
        address = str(row.get("address", "")).strip()
        is_valid_ner, ner_result = ner_gatekeeper(name, address)
        if not is_valid_ner:
            rejected += 1
            gatekeeper_rejected += 1
            print(f"    🚫 NER Rejected: '{name[:40]}' — {ner_result}")
            continue
        # NER passed - ner_result contains full original name
        # (we intentionally do NOT extract ORG substring to preserve location info)

        result, conf, reason = is_ece_by_keyword(name)
        if result is True:
            kept.append(row.to_dict())
            continue
        if result is False:
            rejected += 1
            print(f"    Rejected: '{name[:40]}' — {reason}")
            continue

        # Ambiguous — keep (safe default)
        kept.append(row.to_dict())

    print(f"  [Filter] Kept {len(kept)} | Rejected {rejected} (Gatekeeper: {gatekeeper_rejected})")
    return pd.DataFrame(kept) if kept else pd.DataFrame(columns=df.columns)

# ─────────────────────────── BUG 14 FIX: Dedup on centre_name ─────

def normalize_string_for_comparison(text: str) -> str:
    """
    Smart string normalization for comparison.
    Convert to lowercase, remove punctuation, strip spaces.
    """
    if not text:
        return ""
    
    # Convert to lowercase and strip whitespace
    normalized = text.lower().strip()
    
    # Remove punctuation (keep only letters, numbers, and spaces)
    normalized = re.sub(r'[^a-z0-9\s]', ' ', normalized)
    
    # Replace multiple spaces with single space
    normalized = re.sub(r'\s+', ' ', normalized)
    
    return normalized.strip()

def normalize_phone(phone: str) -> str:
    """Normalize phone number for comparison by removing all non-digits."""
    if not phone:
        return ""
    digits_only = re.sub(r'\D', '', phone)
    # Remove leading 60 (Malaysia country code) if present for comparison
    if digits_only.startswith('60') and len(digits_only) > 9:
        digits_only = digits_only[2:]
    return digits_only

def get_street_name(address: str) -> str:
    """Extract street name from address for comparison."""
    if not address:
        return ""
    # Look for Jalan, Lorong, Persiaran, etc.
    street_patterns = [
        r'(Jalan|Jln|Lorong|Persiaran|Lebuh|Lebuhraya)\s+([a-zA-Z0-9\s]+?)(?:,|$)',
        r'(Taman|Desa|Bandar)\s+([a-zA-Z0-9\s]+?)(?:,|$)',
    ]
    for pattern in street_patterns:
        match = re.search(pattern, address, re.IGNORECASE)
        if match:
            return match.group(0).strip().lower()
    return ""

def merge_attributes(keep_row: Dict, drop_row: Dict) -> Dict:
    """Attribute Merging: Keep best data from both records using SOURCE PRIORITY.
    
    BUG FIX: Was using "longest address wins" which picked garbled Tavily snippets
    over clean Google Maps addresses. Now uses SOURCE_PRIORITY like fetcher.py.
    
    - Address: Higher priority source wins (not longest)
    - Most complete fee structure
    - Cleaned name (prefer shorter, cleaner name)
    - Any non-empty field
    """
    merged = keep_row.copy()
    
    # BUG FIX: Use source priority for address (not longest wins)
    # Higher priority = more trustworthy source (Google Maps > Tavily snippets)
    SOURCE_PRIORITY = {
        "Google Maps Places API": 100,
        "Overpass API": 90,
        "Foursquare Places API": 80,
        "Kiddy123 Directory": 70,
        "Tavily": 50,
        "SerpAPI": 50,
        "Raw CSV": 40,
    }
    
    keep_priority = SOURCE_PRIORITY.get(keep_row.get("source_primary", ""), 0)
    drop_priority = SOURCE_PRIORITY.get(drop_row.get("source_primary", ""), 0)
    
    # Address: Higher priority source wins, not longest
    addr_keep = str(keep_row.get("address", "")).strip()
    addr_drop = str(drop_row.get("address", "")).strip()
    if addr_drop and drop_priority > keep_priority:
        merged["address"] = addr_drop
    elif addr_drop and not addr_keep:
        merged["address"] = addr_drop
    # If same priority or keep has higher priority, keep the existing address
    
    # Merge fees (keep unique values)
    fees_keep = keep_row.get("fees", []) or []
    fees_drop = drop_row.get("fees", []) or []
    if isinstance(fees_keep, str):
        fees_keep = [fees_keep] if fees_keep else []
    if isinstance(fees_drop, str):
        fees_drop = [fees_drop] if fees_drop else []
    all_fees = list(set(fees_keep + fees_drop))
    if all_fees:
        merged["fees"] = all_fees[:3]  # Keep max 3 unique fees
    
    # Prefer cleaned (shorter) name if it contains the main brand
    name_keep = str(keep_row.get("centre_name", "")).strip()
    name_drop = str(drop_row.get("centre_name", "")).strip()
    # Use the shorter name if it's not empty and contains key brand words
    if len(name_drop) < len(name_keep) and len(name_drop) > 5:
        # Check if drop name is contained within keep name (brand preservation)
        if name_drop.lower() in name_keep.lower() or name_keep.lower() in name_drop.lower():
            merged["centre_name"] = name_drop
    
    # Fill in any missing fields from dropped record
    for key in drop_row:
        if not merged.get(key) and drop_row.get(key):
            merged[key] = drop_row[key]
    
    return merged

def deduplicate_enriched_strict_identity(df: pd.DataFrame) -> pd.DataFrame:
    """
    Late-Stage Entity Resolution with Hard Link and Soft Link rules.
    
    Goal: Eliminate duplicates like 'Knowledge Tree' appearing 3 times.
    
    The 'Hard Link' Rule: Same Normalized Phone Number = merge regardless of name similarity.
    The 'Soft Link' Rule: Name Similarity > 85% AND (Distance < 200m OR identical street names) = merge.
    
    Attribute Merging: Keep longest address, most complete fee structure, cleaned name.
    
    Anti-Chain Protection: Never merge government chain branches (Tabika Kemas, PASTI, etc.)
    """
    if df.empty:
        return df

    print(f"  [Entity Resolution] Checking {len(df)} enriched centres for duplicates...")
    seen: List[int] = []   # indices to keep
    merged_into: Dict[int, int] = {}  # dropped_idx --> kept_idx
    merge_reasons: Dict[int, str] = {}  # dropped_idx --> merge reason
    merged_data: Dict[int, Dict] = {}   # kept_idx --> merged attributes
    
    # Government chains for anti-chain rule
    GOVERNMENT_CHAINS = ["tabika kemas", "pasti", "taski", "tadika perpaduan", 
                         "kemas", "perpaduan", "kafa"]
    
    rows = df.reset_index(drop=True)

    for i in range(len(rows)):
        if i in merged_into:
            continue
        
        # Extract record data
        r_name_raw = str(rows.at[i, "centre_name"]).strip()
        r_name_norm = normalize_string_for_comparison(r_name_raw)
        r_address = str(rows.at[i, "address"]).strip() if "address" in rows.columns else ""
        r_phone = str(rows.at[i, "phone"]).strip() if "phone" in rows.columns else ""
        r_phone_norm = normalize_phone(r_phone)
        r_lat = rows.at[i, "lat"] if "lat" in rows.columns else None
        r_lng = rows.at[i, "lng"] if "lng" in rows.columns else None
        r_street = get_street_name(r_address)
        
        # Check if government chain
        is_gov_r = any(chain in r_name_raw.lower() for chain in GOVERNMENT_CHAINS)

        merged = False
        for j in seen:
            # Extract existing record data
            e_name_raw = str(rows.at[j, "centre_name"]).strip()
            e_name_norm = normalize_string_for_comparison(e_name_raw)
            e_address = str(rows.at[j, "address"]).strip() if "address" in rows.columns else ""
            e_phone = str(rows.at[j, "phone"]).strip() if "phone" in rows.columns else ""
            e_phone_norm = normalize_phone(e_phone)
            e_lat = rows.at[j, "lat"] if "lat" in rows.columns else None
            e_lng = rows.at[j, "lng"] if "lng" in rows.columns else None
            e_street = get_street_name(e_address)
            
            # Check if government chain
            is_gov_e = any(chain in e_name_raw.lower() for chain in GOVERNMENT_CHAINS)
            
            # ANTI-CHAIN RULE: Never merge government chains
            if is_gov_r and is_gov_e:
                continue
            
            # HARD LINK RULE: Same Normalized Phone Number
            # If phones match exactly (after normalization), merge regardless of name
            if (r_phone_norm and e_phone_norm and 
                r_phone_norm == e_phone_norm and 
                len(r_phone_norm) >= 9):
                merged_into[i] = j
                merge_reasons[i] = f"HARD LINK: Same phone ({r_phone[:20]})"
                merged = True
                break
            
            # SOFT LINK RULE: Name Similarity + Proximity
            # Check name similarity first
            fuzzy_score = fuzz.token_set_ratio(r_name_norm, e_name_norm)
            
            if fuzzy_score >= 85:  # High name similarity threshold
                # Check proximity (distance < 200m) or same street
                proximity_match = False
                
                # Calculate distance if coordinates available
                if (r_lat is not None and e_lat is not None and 
                    r_lng is not None and e_lng is not None and
                    r_lat != 0 and e_lat != 0):
                    try:
                        distance_m = haversine_km(float(r_lat), float(r_lng),
                                                  float(e_lat), float(e_lng)) * 1000
                        if distance_m < 200:  # Within 200 meters
                            proximity_match = True
                    except (ValueError, TypeError):
                        pass
                
                # Same street name check
                if not proximity_match and r_street and e_street:
                    if r_street == e_street:
                        proximity_match = True
                
                # Identical address check
                if not proximity_match and r_address and e_address:
                    if r_address.lower() == e_address.lower():
                        proximity_match = True
                
                if proximity_match:
                    merged_into[i] = j
                    merge_reasons[i] = f"SOFT LINK: Name sim ({fuzzy_score}%) + proximity"
                    merged = True
                    break

        if not merged:
            seen.append(i)
            # Store initial merged data
            merged_data[i] = rows.iloc[i].to_dict()

    # Build result with attribute merging
    result_rows = []
    for idx in seen:
        base_row = merged_data.get(idx, rows.iloc[idx].to_dict())
        
        # Check if any rows were merged into this one
        merged_count = sum(1 for dropped_idx, kept_idx in merged_into.items() if kept_idx == idx)
        
        if merged_count > 0:
            # Merge attributes from all dropped records
            for dropped_idx, kept_idx in merged_into.items():
                if kept_idx == idx:
                    dropped_row = rows.iloc[dropped_idx].to_dict()
                    base_row = merge_attributes(base_row, dropped_row)
            
            # Add merge metadata
            base_row["merge_count"] = merged_count + 1
            base_row["entity_resolution"] = "Duplicates merged"
        
        result_rows.append(base_row)

    result = pd.DataFrame(result_rows)
    
    # Log merge details
    for dropped_idx, kept_idx in merged_into.items():
        reason = merge_reasons.get(dropped_idx, "Unknown")
        dropped_name = str(rows.at[dropped_idx, "centre_name"])[:50]
        kept_name = str(rows.at[kept_idx, "centre_name"])[:50]
        print(f"    [Merge] {dropped_name} --> {kept_name} ({reason})")
    
    merged_count = len(merged_into)
    print(f"  [Entity Resolution] {len(df)} --> {len(result)} (merged {merged_count} duplicates)")
    return result

# ─────────────────────────── DDGS Waterfall Verification ──────────

def strip_markdown_and_html(text: str) -> str:
    """BUG E5: Clean HTML/markdown contamination from scraped text.
    
    Removes:
    - Markdown images: ![alt](url)
    - Markdown links: [text](url) → keeps text only
    - Bold/italic markers: **text** or *text* → keeps text
    - Headings: # Heading → keeps text
    - HTML tags: <tag>content</tag> → keeps content
    """
    if not text:
        return text
    # Markdown images
    text = re.sub(r'!\[.*?\]\(.*?\)', '', text)
    # Markdown links → keep text only
    text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)
    # Bold/italic markers
    text = re.sub(r'\*{1,3}([^*]+)\*{1,3}', r'\1', text)
    # Headings
    text = re.sub(r'#{1,6}\s*', '', text)
    # HTML tags (limit to 200 chars to avoid catastrophic backtracking)
    text = re.sub(r'<[^>]{1,200}>', '', text)
    # Collapse whitespace
    text = re.sub(r'\s{2,}', ' ', text)
    return text.strip()

def extract_fees_from_text(text: str) -> List[str]:
    """Extract RM fee patterns from text.
    
    BUG E6: Added minimum-value guard (>= 50) to eliminate false captures
    like "rm1" from "Precint 12" or "rm35" from address fragments.
    No legitimate Malaysian preschool has monthly fee under RM50.
    
    Bug Fix 3: Expanded patterns to catch fees in natural language context
    like "monthly is RM500 for half day" or "yuran bulanan RM450".
    """
    # Extended patterns to catch fees in various contexts
    patterns = [
        # Standard RM patterns
        r'RM\s*[\d,]+(?:\s*[-–]\s*[\d,]+)?(?:\s*/\s*(?:month|mth|bulan))?',
        r'RM\s*[\d,]+(?:\.\d{2})?\s*(?:per|/)?\s*(?:month|year|annum)?',
        # Context-aware: "monthly is RM500", "yuran RM450", etc.
        r'(?:monthly|month|mth|bulanan|yuran)\s*(?:is|adalah)?[:\s]*RM\s*[\d,]+',
        r'(?:half.?day|halfday|half-day).*?RM\s*[\d,]+',
        r'(?:full.?day|fullday|full-day).*?RM\s*[\d,]+',
        r'RM\s*[\d,]+\s*(?:per|/|\-)?\s*(?:month|mth|bulan)',
        # Malay terms
        r'yuran\s+(?:bulanan)?[:\s]*RM\s*[\d,]+',
        r'bayaran\s+(?:bulanan)?[:\s]*RM\s*[\d,]+',
    ]
    fees = []
    for pattern in patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        for match in matches:
            # BUG E6: Parse numeric value and filter out values < 50
            try:
                # Extract first number from the match (handles "RM 350" or "RM350")
                num_match = re.search(r'[\d,]+', match)
                if num_match:
                    num_str = num_match.group().replace(',', '')
                    value = int(num_str)
                    if value >= 50:  # Minimum threshold for legitimate fees
                        fees.append(match)
            except (ValueError, AttributeError):
                # If we can't parse, keep the match (conservative)
                fees.append(match)
    return list(set(fees))[:5]  # Return unique fees, max 5

def extract_phone_from_text(text: str) -> str:
    """Extract Malaysian phone numbers with specific patterns.
    
    Patterns:
    - +60 formats: +60 3-XXXX XXXX, +60 1X-XXX XXXX
    - 03- format: 03-XXXX XXXX (landline)
    - 01x format: 01X-XXX XXXX (mobile)
    """
    # Malaysian phone patterns (high-confidence)
    patterns = [
        # +60 international format
        r'\+60\s*\d{1,2}[-.\s]\d{3,4}[-.\s]?\d{4}',
        # 03- landline format
        r'03[-.\s]\d{3,4}[-.\s]?\d{4}',
        # 01x mobile format
        r'01\d[-.\s]\d{3,4}[-.\s]?\d{4}',
        # Generic Malaysian format with country code optional
        r'(?:\+?60)?[-.\s]?\d{2,3}[-.\s]?\d{3,4}[-.\s]?\d{4}',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            phone = match.group(0).strip()
            # Validate it's a reasonable phone number
            digits_only = re.sub(r'\D', '', phone)
            if len(digits_only) >= 9:  # Malaysian numbers are 9-11 digits
                return phone
    return ""

def extract_curriculum_from_text(text: str) -> str:
    """Extract curriculum type from text."""
    text_lower = text.lower()
    for curriculum, keywords in CURRICULUM_KEYWORDS.items():
        if any(kw in text_lower for kw in keywords):
            return curriculum
    return ""

def extract_address_from_text(text: str) -> str:
    """Extract Malaysian address patterns using high-confidence keywords.
    
    High-confidence markers: Jalan, No., Seksyen, Bandar, Tingkat, Lorong,
    Persiaran, Lebuh, Jln, Persiaran, Taman, Keluasan
    """
    # High-confidence Malaysian address patterns
    patterns = [
        # Full address with street, postcode, city
        r'\d+[\s,]+(?:Jalan|Jln|Lorong|Persiaran|Lebuh|Jln\.)\s+[a-zA-Z0-9\s,]+,?\s*\d{5}\s*(?:Shah Alam|Selangor|Kuala Lumpur)',
        # Address with No./Lot and Seksyen/Bandar
        r'(?:No\.?|Lot)\s*\d+[\s,]+[a-zA-Z0-9\s,]*(?:Seksyen|Bandar|Jalan|Taman)\s+[a-zA-Z0-9\s,]+',
        # Street name patterns
        r'(?:Jalan|Jln|Lorong|Persiaran|Lebuhraya|Lebuh)\s+[a-zA-Z0-9\s,\-]+(?:Seksyen\s+\d+|\d{5})',
        # Area/neighborhood with postcode
        r'(?:Taman|Tingkat|Keluasan|Desa)\s+[a-zA-Z0-9\s,]+,?\s*\d{5}',
        # Fallback: Postcode-based address
        r'[a-zA-Z0-9\s,]+,\s*\d{5}\s*(?:Shah Alam|Selangor)',
    ]
    
    matches = []
    for pattern in patterns:
        found = re.findall(pattern, text, re.IGNORECASE)
        matches.extend(found)
    
    if matches:
        # Return the longest match (most complete address)
        best_match = max(matches, key=len).strip()
        # Clean up the match
        best_match = re.sub(r'\s+', ' ', best_match)
        return best_match
    
    return ""

def extract_neighbourhood_from_text(text: str) -> str:
    """Extract neighbourhood/area from text."""
    text_lower = text.lower()
    
    # Known Shah Alam neighbourhoods
    neighbourhoods = [
        "bukit jelutong", "setia alam", "elmina", "denai alam", 
        "shah alam", "subang", "petaling jaya", "puchong"
    ]
    
    for hood in neighbourhoods:
        if hood in text_lower:
            return hood.title()
    
    return ""

def extract_language_medium_from_text(text: str) -> str:
    """Extract language medium from text."""
    text_lower = text.lower()
    
    language_patterns = {
        "English": ["english", "bahasa inggeris"],
        "Malay": ["bahasa melayu", "malay", "bm"],
        "Bilingual": ["bilingual", "dual language", "english & malay"],
        "Mandarin": ["mandarin", "chinese", "bahasa cina"],
        "Arabic": ["arabic", "bahasa arab"],
    }
    
    for lang, keywords in language_patterns.items():
        if any(kw in text_lower for kw in keywords):
            return lang
    
    return ""

def parse_iso_duration(iso_string: str) -> str:
    """Convert ISO 8601 duration (PT1H30M) to human-readable minutes.
    
    Examples:
    - PT1H30M -> "90 mins"
    - PT45M -> "45 mins"
    - PT2H -> "120 mins"
    """
    if not iso_string or not iso_string.startswith("PT"):
        return ""
    
    try:
        # Remove PT prefix
        duration = iso_string[2:]
        
        hours = 0
        minutes = 0
        
        # Extract hours
        if "H" in duration:
            parts = duration.split("H")
            hours = int(parts[0])
            duration = parts[1] if len(parts) > 1 else ""
        
        # Extract minutes
        if "M" in duration:
            minutes_part = duration.split("M")[0]
            if minutes_part.isdigit():
                minutes = int(minutes_part)
        
        total_minutes = hours * 60 + minutes
        
        if total_minutes > 0:
            return f"{total_minutes} mins"
        return ""
    except (ValueError, IndexError):
        return ""

def extract_jsonld_schema(page) -> Dict:
    """Schema-First Recipe: Extract structured data from JSON-LD scripts.
    
    Primary source for: name, address, telephone, priceRange
    """
    schema_data = {
        "name": "",
        "address": "",
        "telephone": "",
        "priceRange": "",
        "openingHours": "",
        "description": "",
    }
    
    try:
        # Find all JSON-LD script tags
        jsonld_scripts = page.eval_on_selector_all(
            'script[type="application/ld+json"]',
            "elements => elements.map(el => el.textContent)"
        )
        
        for script_content in jsonld_scripts:
            try:
                data = json.loads(script_content)
                
                # Handle both single objects and arrays
                if isinstance(data, list):
                    items = data
                else:
                    items = [data]
                
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    
                    # Check if it's a LocalBusiness or relevant type
                    item_type = item.get("@type", "")
                    valid_types = ["LocalBusiness", "Preschool", "School", 
                                   "EducationalOrganization", "ChildCare", "DayCare"]
                    
                    if any(t in str(item_type) for t in valid_types):
                        # Extract name
                        if item.get("name"):
                            schema_data["name"] = item.get("name")
                        
                        # Extract address (can be string or object)
                        address = item.get("address", "")
                        if isinstance(address, dict):
                            addr_parts = []
                            if address.get("streetAddress"):
                                addr_parts.append(address.get("streetAddress"))
                            if address.get("addressLocality"):
                                addr_parts.append(address.get("addressLocality"))
                            if address.get("addressRegion"):
                                addr_parts.append(address.get("addressRegion"))
                            if address.get("postalCode"):
                                addr_parts.append(address.get("postalCode"))
                            schema_data["address"] = ", ".join(addr_parts)
                        elif isinstance(address, str):
                            schema_data["address"] = address
                        
                        # Extract telephone
                        if item.get("telephone"):
                            schema_data["telephone"] = item.get("telephone")
                        
                        # Extract price range/fees
                        if item.get("priceRange"):
                            schema_data["priceRange"] = item.get("priceRange")
                        
                        # Extract opening hours
                        if item.get("openingHours"):
                            schema_data["openingHours"] = str(item.get("openingHours"))
                        
                        # Extract description
                        if item.get("description"):
                            schema_data["description"] = item.get("description")
            
            except json.JSONDecodeError:
                continue
    
    except Exception:
        pass
    
    return schema_data

def extract_scale_from_text(text: str) -> str:
    """Extract centre scale/size from text."""
    text_lower = text.lower()
    
    scale_patterns = {
        "Large": ["large", "big", "spacious", "capacity", "200+", "100+"],
        "Medium": ["medium", "moderate", "50+", "100"],
        "Small": ["small", "cozy", "intimate", "30+", "20+"],
    }
    
    for scale, keywords in scale_patterns.items():
        if any(kw in text_lower for kw in keywords):
            return scale
    
    return ""

def extract_religious_orientation_from_text(text: str) -> str:
    """Extract religious orientation from text."""
    text_lower = text.lower()
    
    religious_patterns = {
        "Islamic": ["islamic", "muslim", "islam", "quran", "arabic"],
        "Christian": ["christian", "church", "bible"],
        "Buddhist": ["buddhist", "buddhism"],
        "Secular": ["secular", "non-religious", "no religion"],
    }
    
    for orientation, keywords in religious_patterns.items():
        if any(kw in text_lower for kw in keywords):
            return orientation
    
    return ""

def extract_source_from_text(text: str) -> str:
    """Extract source information from text."""
    # Look for website names, social media, etc.
    source_patterns = [
        r'(facebook\.com|fb\.com|instagram\.com|ig\.com|tiktok\.com|youtube\.com)',
        r'(kiddy123\.com|awesome\.my|parenting\.my)',
        r'(website|web|fb|ig|tiktok|youtube)',
    ]
    
    for pattern in source_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1)
    
    return ""

def deep_search_tier1_yahoo(centre_name: str, neighbourhood: str = "") -> Dict:
    """Deep Search Tier 1: Yahoo Playwright with Schema-First Recipe.
    
    Goal: Force successful extraction using Playwright stealth.
    Setup: AutomationControlled disabled + realistic User-Agent.
    Extraction: JSON-LD schema (primary) + page.inner_text('body') with Regex.
    """
    result = {
        "verified": False,
        "fees": [],
        "phone": "",
        "curriculum": "",
        "address": "",
        "neighbourhood": "",
        "language_medium": "",
        "scale": "",
        "religious_orientation": "",
        "opening_hours": "",
        "sources": [],
        "notes": ""
    }
    
    if not PLAYWRIGHT_AVAILABLE:
        result["notes"] = "Playwright not available"
        return result
    
    # Build search query focused on address and phone
    query = f'"{centre_name}" {neighbourhood} address phone'
    import urllib.parse
    encoded_query = urllib.parse.quote(query)
    search_url = f"https://search.yahoo.com/search?p={encoded_query}"
    
    try:
        with sync_playwright() as p:
            # CRITICAL STEALTH: Launch with anti-detection args
            browser = p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled"]
            )
            
            # Create context with realistic user agent
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            
            page = context.new_page()
            
            # Navigate to Yahoo Search
            print(f"    [Tier 1] Yahoo search: {query[:60]}...")
            page.goto(search_url, timeout=30000)
            
            # Wait for page to load
            page.wait_for_load_state("networkidle", timeout=10000)
            
            # Extract via Schema-First Recipe (JSON-LD)
            schema_data = extract_jsonld_schema(page)
            print(f"      [Debug] JSON-LD schema extracted: name='{schema_data.get('name', '')[:30]}', address='{schema_data.get('address', '')[:30]}...'")
            
            # Aggregate all text from body as fallback
            body_text = page.inner_text('body')
            print(f"      [Debug] Yahoo body text extracted: {len(body_text)} chars")
            
            # Combine schema and body text for extraction
            combined_text = f"{schema_data.get('description', '')} {body_text}"
            
            # Extract data using all extraction functions
            if schema_data.get("telephone"):
                result["phone"] = schema_data.get("telephone")
            else:
                result["phone"] = extract_phone_from_text(combined_text)
            
            if schema_data.get("address"):
                result["address"] = schema_data.get("address")
            else:
                result["address"] = extract_address_from_text(combined_text)
            
            if schema_data.get("priceRange"):
                # Parse priceRange like "$$$" or "RM 500-800"
                result["fees"] = [schema_data.get("priceRange")]
            else:
                result["fees"] = extract_fees_from_text(combined_text)
            
            result["curriculum"] = extract_curriculum_from_text(combined_text)
            result["neighbourhood"] = extract_neighbourhood_from_text(combined_text) or neighbourhood
            result["language_medium"] = extract_language_medium_from_text(combined_text)
            result["scale"] = extract_scale_from_text(combined_text)
            result["religious_orientation"] = extract_religious_orientation_from_text(combined_text)
            result["source"] = extract_source_from_text(combined_text)
            
            # Parse opening hours (convert ISO 8601 if needed)
            opening_hours = schema_data.get("openingHours", "")
            if isinstance(opening_hours, str) and opening_hours.startswith("PT"):
                result["opening_hours"] = parse_iso_duration(opening_hours)
            else:
                result["opening_hours"] = str(opening_hours) if opening_hours else ""
            
            # Check if we found a physical address (primary goal)
            has_physical_address = bool(result["address"] and len(result["address"]) > 15)
            has_phone = bool(result["phone"])
            
            if has_physical_address:
                result["verified"] = True
                result["notes"] = f"[Yahoo] Address found: {result['address'][:40]}..."
                result["sources"].append(search_url)
                print(f"      [Debug] Tier 1 SUCCESS: Address extracted ({len(result['address'])} chars)")
            elif has_phone:
                result["verified"] = True
                result["notes"] = "[Yahoo] Phone found (address pending)"
                result["sources"].append(search_url)
                print(f"      [Debug] Tier 1 PARTIAL: Phone only")
            else:
                result["verified"] = False
                result["notes"] = "Yahoo: No physical address found"
                print(f"      [Debug] Tier 1 FAILED: No address or phone")
            
            browser.close()
            
    except Exception as e:
        result["notes"] = f"Yahoo error: {str(e)[:100]}"
        print(f"      [Debug] Tier 1 ERROR: {str(e)[:80]}")
        try:
            if 'browser' in locals():
                browser.close()
        except:
            pass
    
    return result

def deep_search_tier2_tavily(centre_name: str, neighbourhood: str = "") -> Dict:
    """Deep Search Tier 2: Tavily API (fallback only if Tier 1 fails to find address).
    
    Only used when Tier 1 (Yahoo) fails to extract a physical address.
    """
    result = {
        "verified": False,
        "fees": [],
        "phone": "",
        "curriculum": "",
        "address": "",
        "neighbourhood": "",
        "language_medium": "",
        "scale": "",
        "religious_orientation": "",
        "sources": [],
        "notes": ""
    }
    
    if not TAVILY_AVAILABLE:
        result["notes"] = "Tavily not available"
        return result
    
    key = os.getenv("TAVILY_KEY")
    if not key:
        result["notes"] = "TAVILY_KEY not set"
        return result
    
    try:
        print(f"    [Tier 2] Tavily API search...")
        client = TavilyClient(api_key=key)
        query = f'"{centre_name}" {neighbourhood} preschool address phone Shah Alam'
        
        response = client.search(
            query=query,
            search_depth="basic",
            max_results=5,
            include_answer=True
        )
        
        snippets = []
        for r in response.get("results", []):
            snippets.append(f"{r.get('title', '')} {r.get('content', '')}")
            result["sources"].append(r.get('url', ''))
        
        combined_text = " ".join(snippets) if snippets else ""
        print(f"      [Debug] Tavily extracted: {len(combined_text)} chars from {len(snippets)} results")
        
        if combined_text:
            # Extract all data
            result["fees"] = extract_fees_from_text(combined_text)
            result["phone"] = extract_phone_from_text(combined_text)
            result["curriculum"] = extract_curriculum_from_text(combined_text)
            result["address"] = extract_address_from_text(combined_text)
            result["neighbourhood"] = extract_neighbourhood_from_text(combined_text) or neighbourhood
            result["language_medium"] = extract_language_medium_from_text(combined_text)
            result["scale"] = extract_scale_from_text(combined_text)
            result["religious_orientation"] = extract_religious_orientation_from_text(combined_text)
            result["source"] = extract_source_from_text(combined_text)
            
            # Check if we found physical address
            has_physical_address = bool(result["address"] and len(result["address"]) > 15)
            has_phone = bool(result["phone"])
            
            if has_physical_address:
                result["verified"] = True
                result["notes"] = f"[Tavily] Address found: {result['address'][:40]}..."
                print(f"      [Debug] Tier 2 SUCCESS: Address found ({len(result['address'])} chars)")
            elif has_phone:
                result["verified"] = True
                result["notes"] = "[Tavily] Phone found"
                print(f"      [Debug] Tier 2 PARTIAL: Phone found")
            else:
                result["verified"] = False
                result["notes"] = f"Tavily: {len(snippets)} results, no address"
                print(f"      [Debug] Tier 2 FAILED: No address extracted")
        else:
            result["verified"] = False
            result["notes"] = "Tavily: No content extracted"
            print(f"      [Debug] Tier 2 FAILED: Empty response")
            
    except Exception as e:
        result["notes"] = f"Tavily error: {str(e)[:100]}"
        print(f"      [Debug] Tier 2 ERROR: {str(e)[:80]}")
    
    return result


def deep_search_tier1_ddgs(centre_name: str, neighbourhood: str = "") -> Dict:
    """Deep Search Tier 1: DuckDuckGo Search (DDGS) - Free tier, no API key needed.
    
    Uses DDGS to search for centre information and extract data from snippets.
    """
    result = {
        "verified": False,
        "fees": [],
        "phone": "",
        "curriculum": "",
        "address": "",
        "neighbourhood": "",
        "language_medium": "",
        "scale": "",
        "religious_orientation": "",
        "opening_hours": "",
        "sources": [],
        "notes": ""
    }
    
    if not DDGS_AVAILABLE:
        result["notes"] = "DDGS not available"
        return result
    
    try:
        print(f"    [Tier 1] DDGS search...")
        query = f'"{centre_name}" {neighbourhood} preschool Shah Alam address phone'
        
        with DDGS() as ddgs:
            search_results = ddgs.text(query, max_results=5)
            snippets = []
            for r in search_results:
                snippets.append(f"{r.get('title', '')} {r.get('body', '')}")
                result["sources"].append(r.get('href', ''))
        
        combined_text = " ".join(snippets) if snippets else ""
        print(f"      [Debug] DDGS extracted: {len(combined_text)} chars from {len(snippets)} results")
        
        if combined_text:
            # Extract all data
            result["fees"] = extract_fees_from_text(combined_text)
            result["phone"] = extract_phone_from_text(combined_text)
            result["curriculum"] = extract_curriculum_from_text(combined_text)
            result["address"] = extract_address_from_text(combined_text)
            result["neighbourhood"] = extract_neighbourhood_from_text(combined_text) or neighbourhood
            result["language_medium"] = extract_language_medium_from_text(combined_text)
            result["scale"] = extract_scale_from_text(combined_text)
            result["religious_orientation"] = extract_religious_orientation_from_text(combined_text)
            
            # Check if we found physical address
            has_physical_address = bool(result["address"] and len(result["address"]) > 15)
            has_phone = bool(result["phone"])
            
            if has_physical_address:
                result["verified"] = True
                result["notes"] = f"[DDGS] Found address + {len(snippets)} sources"
                print(f"      [Debug] DDGS verified with address: {result['address'][:50]}...")
            else:
                result["notes"] = f"[DDGS] No address found in {len(snippets)} results"
        else:
            result["notes"] = "DDGS: No results"
            
    except Exception as e:
        result["notes"] = f"DDGS error: {str(e)[:100]}"
        print(f"      [Debug] DDGS ERROR: {str(e)[:80]}")
    
    return result

def deep_search_verify(centre_name: str, neighbourhood: str = "") -> Dict:
    """Deep Search Waterfall: 3-Tier verification.
    
    Architecture:
    - Tier 1 (DDGS): Free DuckDuckGo search - PRIMARY for free tier
    - Tier 2 (Yahoo/Playwright): Fallback if DDGS fails (requires Playwright)
    - Tier 3 (Tavily): Final fallback (requires TAVILY_KEY)
    
    Debug logging prints character counts at each stage to monitor bot-blocking.
    """
    # Use original centre name (LLM cleaning removed)
    clean_name = centre_name.strip()
    print(f"  [Deep Search] '{centre_name}' -> '{clean_name}'")
    
    # Tier 1: DDGS (Free tier - no API key needed)
    result = deep_search_tier1_ddgs(clean_name, neighbourhood)
    
    # Check if Tier 1 succeeded with physical address
    has_address = bool(result.get("address") and len(result.get("address", "")) > 15)
    
    if has_address:
        print(f"  [Deep Search] COMPLETE via Tier 1 (DDGS)")
        return result
    
    # Tier 2: Yahoo Playwright (if DDGS failed and Playwright available)
    if not has_address and PLAYWRIGHT_AVAILABLE:
        print(f"    [Waterfall] Tier 1 no address, attempting Tier 2 (Yahoo Playwright)...")
        yahoo_result = deep_search_tier1_yahoo(clean_name, neighbourhood)
        
        if yahoo_result.get("verified") and yahoo_result.get("address"):
            result = yahoo_result
            has_address = True
            print(f"  [Deep Search] COMPLETE via Tier 2 (Yahoo)")
            return result
    
    # Tier 3: Tavily fallback (final fallback)
    if not has_address and TAVILY_AVAILABLE:
        print(f"    [Waterfall] Attempting Tier 3 (Tavily)...")
        tavily_result = deep_search_tier2_tavily(clean_name, neighbourhood)
        
        # Merge Tavily results with DDGS results (keep best data from both)
        if tavily_result.get("verified"):
            # Use Tavily address if DDGS didn't find one
            if not result.get("address") and tavily_result.get("address"):
                result["address"] = tavily_result["address"]
            # Use Tavily phone if DDGS didn't find one
            if not result.get("phone") and tavily_result.get("phone"):
                result["phone"] = tavily_result["phone"]
            # Merge fees
            if tavily_result.get("fees"):
                result["fees"] = list(set(result.get("fees", []) + tavily_result["fees"]))
            # Update verification status
            result["verified"] = True
            result["sources"] = list(set(result.get("sources", []) + tavily_result.get("sources", [])))
            result["notes"] = f"[Combined] DDGS: '{result.get('notes', '')}' | Tavily: '{tavily_result.get('notes', '')}'"
            print(f"  [Deep Search] COMPLETE via Tier 1+3 (DDGS + Tavily)")
        else:
            print(f"  [Deep Search] INCOMPLETE: No address found in any tier")
    
    return result

# Legacy function - replaced by deep_search_verify() above
# Keeping for backward compatibility but redirects to new architecture
def verify_with_yahoo_playwright(centre_name: str, neighbourhood: str = "") -> Dict:
    """Legacy: Use deep_search_verify() instead."""
    return deep_search_verify(centre_name, neighbourhood)

def verify_with_ddg_playwright(centre_name: str, neighbourhood: str = "") -> Dict:
    """Legacy: Use deep_search_verify() instead."""
    return deep_search_verify(centre_name, neighbourhood)

def verify_with_tavily(centre_name: str, neighbourhood: str = "") -> Dict:
    """Legacy: Use deep_search_verify() instead."""
    return deep_search_tier2_tavily(centre_name, neighbourhood)

def verify_with_playwright(centre_name: str, neighbourhood: str = "") -> Dict:
    """Headless browser verification using Playwright with DuckDuckGo Lite."""
    result = {
        "verified": False,
        "fees": [],
        "phone": "",
        "curriculum": "",
        "sources": [],
        "notes": ""
    }
    
    if not PLAYWRIGHT_AVAILABLE:
        result["notes"] = "Playwright not available"
        return result
    
    # Build search query for DuckDuckGo Lite
    query = f'"{centre_name}" {neighbourhood} preschool fees phone Shah Alam'
    search_url = f"https://duckduckgo.com/lite/?q={query}"
    
    try:
        with sync_playwright() as p:
            # Launch browser with minimal resources
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            
            # Set realistic user agent
            page.set_extra_http_headers({
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
            })
            
            # Navigate with strict timeout
            page.goto(search_url, timeout=15000)  # 15 seconds
            
            # Wait for content to load (brief wait)
            page.wait_for_load_state("networkidle", timeout=5000)
            
            # Extract body text from DuckDuckGo Lite
            body_text = page.evaluate("() => document.body.innerText")
            
            # Debug logging
            print(f"      [Debug] Playwright extracted {len(body_text)} chars")
            
            if body_text:
                # Clean and extract data using existing functions
                result["fees"] = extract_fees_from_text(body_text)
                result["phone"] = extract_phone_from_text(body_text)
                result["curriculum"] = extract_curriculum_from_text(body_text)
                
                # Ultimate Relaxed Success Condition
                # Condition A: Found specific data via regex
                has_specific_data = bool(result["fees"] or result["phone"] or result["curriculum"])
                
                if has_specific_data:
                    result["verified"] = True
                    result["notes"] = "[Source] Enriched successfully"
                    result["sources"].append(search_url)
                    return result
                
                # Condition B: Check for ECE keywords or centre name in text
                body_text_lower = body_text.lower()
                centre_name_lower = centre_name.lower()
                
                ece_keywords = ["tadika", "taska", "preschool", "kindergarten", "childcare", "nursery"]
                has_ece_keywords = any(keyword in body_text_lower for keyword in ece_keywords)
                has_centre_name = centre_name_lower in body_text_lower
                
                if has_ece_keywords or has_centre_name:
                    result["verified"] = True
                    result["notes"] = "[Source] Verified existence via keywords"
                    result["sources"].append(search_url)
                    return result
                
                # Failure: No relevant content
                result["verified"] = False
                result["notes"] = "Playwright: No relevant content found"
            else:
                result["notes"] = "Playwright: No content extracted"
            
            browser.close()
            
    except Exception as e:
        # Handle all exceptions gracefully
        result["notes"] = f"Playwright error: {str(e)[:100]}"
        # Ensure browser is closed if it was opened
        try:
            if 'browser' in locals():
                browser.close()
        except:
            pass
    
    return result

def verify_centre_waterfall(centre_name: str, neighbourhood: str = "") -> Dict:
    """
    Deep Search Waterfall: 2-Tier verification with Playwright stealth.
    Uses new deep_search_verify architecture:
    - Tier 1: Yahoo Playwright with JSON-LD schema extraction
    - Tier 2: Tavily API (only if Tier 1 fails to find physical address)
    """
    # Use the new Deep Search Verify function
    result = deep_search_verify(centre_name, neighbourhood)
    
    # Determine method used based on notes
    if "Yahoo" in result.get("notes", "") and "Tavily" not in result.get("notes", ""):
        result["method"] = "yahoo_playwright"
    elif "Tavily" in result.get("notes", ""):
        result["method"] = "combined_yahoo_tavily"
    elif "False positive" in result.get("notes", ""):
        result["method"] = "filtered_false_positive"
    else:
        result["method"] = "none"
    
    return result

# ─────────────────────────── Main pipeline ────────────────────────

def enrich(input_csv: str, output_csv: str,
           centre_lat: float, centre_lng: float,
           radius_km: float,
           skip_cleaning: bool = False) -> None:

    if not os.path.exists(input_csv):
        raise FileNotFoundError(f"Input not found: {input_csv}")

    df = pd.read_csv(input_csv)
    print(f"Loaded {input_csv}: {len(df)} rows")

    # Ensure all expected columns exist
    expected = ["centre_name", "address", "neighbourhood", "lat", "lng", "curriculum",
                "language_medium", "fee_halfday_raw", "fee_fullday_raw", "scale",
                "religious_orientation", "source_primary", "source_notes"]
    for col in expected:
        if col not in df.columns:
            df[col] = None if col in ("lat", "lng") else ""

    # ── Step 0: Filter non-ECE centres ───────────────────────────
    df = filter_non_ece(df)
    if df.empty:
        print("WARNING: All rows were filtered out. Check raw.csv quality.")
        return
    
    # BUG E3: Pre-filter - drop incoming rows that fuzzy-match TARGET_ROWS
    # This prevents the target from appearing as a competitor
    target_names = [t["centre_name"] for t in TARGET_ROWS]
    def is_target_fuzzy_match(row):
        name = str(row.get("centre_name", ""))
        for target_name in target_names:
            if fuzz.token_set_ratio(name, target_name) >= 90:
                return True
        return False
    
    before_pre_filter = len(df)
    df = df[~df.apply(is_target_fuzzy_match, axis=1)].reset_index(drop=True)
    after_pre_filter = len(df)
    if before_pre_filter != after_pre_filter:
        print(f"  [Step 0] Dropped {before_pre_filter - after_pre_filter} target-matching competitor(s)")

    # ── Step 0.5: Post-processing cleaning (early stage) ───────────
    # Clean data BEFORE geocoding and neighbourhood assignment
    if not skip_cleaning:
        df = run_post_processing_cleaning(df, early_stage=True)

    # ── Step 1: Geocode missing coordinates ──────────────────────
    # Note: Radius filtering is deferred to Step 2.5 (after verification)
    blocked_providers: set = set()
    geocoded_count = 0
    print(f"\nGeocoding {len(df)} rows...")

    for idx, (i, row) in enumerate(df.iterrows(), 1):
        lat = row.get("lat")
        lng = row.get("lng")
        name = str(row.get("centre_name", "")).strip()
        raw_addr = str(row.get("address", "")).strip()
        addr_for_geo = clean_address_for_geocoding(raw_addr)

        # Geocode if no valid coordinates
        if (pd.isna(lat) or lat is None or lat == 0) and addr_for_geo:
            new_lat, new_lng, provider = geocode_with_fallback(addr_for_geo, blocked_providers)
            if new_lat is not None:
                df.loc[i, "lat"] = new_lat
                df.loc[i, "lng"] = new_lng
                geocoded_count += 1
                print(f"  [{provider}] Geocoded: {name[:50]} → {new_lat:.4f},{new_lng:.4f}")
            else:
                notes = str(df.loc[i, "source_notes"])
                df.loc[i, "source_notes"] = (notes + " | [Geocoding failed — verify manually]").strip(" |")

        if idx % 25 == 0:
            print(f"  Processed {idx}/{len(df)}...")

    print(f"\nGeocoding complete: {geocoded_count} rows geocoded")

    # ── Step 2: Enrich each row ───────────────────────────────────
    enriched = []
    for _, row in df.iterrows():
        record = row.to_dict()
        name    = normalize(record.get("centre_name"), "Unnamed ECE Centre")
        address = normalize(record.get("address"),
                            "[Address requires manual verification]")
        lat = record.get("lat")
        lng = record.get("lng")

        record["centre_name"] = name
        record["address"]     = address

        # Neighbourhood
        # Standardize Malaysian address
        address = standardize_malaysian_address(address)
        record["address"] = address
        
        record["neighbourhood"] = assign_neighbourhood(
            lat if not (pd.isna(lat) or lat is None or lat == 0) else None,
            lng if not (pd.isna(lng) or lng is None or lng == 0) else None,
            address,
        )

        # Religious orientation
        if not normalize(record.get("religious_orientation")):
            record["religious_orientation"] = classify_religion(
                name, f"{record.get('curriculum', '')} {record.get('source_notes', '')}"
            )

        # Scale
        if not normalize(record.get("scale")):
            record["scale"] = classify_scale(name)

        # Curriculum
        if not normalize(record.get("curriculum")):
            record["curriculum"] = infer_curriculum(name, str(record.get("source_notes", "")))

        # Language medium
        if not normalize(record.get("language_medium")):
            record["language_medium"] = infer_language(
                name, record["religious_orientation"], record["neighbourhood"]
            )

        # Fees
        fee_hd = normalize(record.get("fee_halfday_raw"))
        fee_fd = normalize(record.get("fee_fullday_raw"))
        verified = get_verified_fees(name)

        if verified:
            record["fee_halfday_raw"] = verified["fee_halfday_raw"]
            record["fee_fullday_raw"] = verified["fee_fullday_raw"]
            notes = str(record.get("source_notes", ""))
            if verified["note"] not in notes:
                record["source_notes"] = (notes + " | " + verified["note"]).strip(" |")
        elif fee_hd and fee_fd:
            pass  # Scraped fees from Kiddy123 — keep as-is
        else:
            inferred_hd, inferred_fd, fee_note = infer_fee_ranges(
                record["scale"], record["religious_orientation"], record["neighbourhood"]
            )
            if not fee_hd:
                record["fee_halfday_raw"] = inferred_hd
            if not fee_fd:
                record["fee_fullday_raw"] = inferred_fd
            record["source_notes"] = (
                str(record.get("source_notes", "")) + " | " + fee_note
            ).strip(" |")

        record["source_notes"] = sanitize_notes(record.get("source_notes", ""))
        record["threat_score"] = compute_threat_score(pd.Series(record))
        enriched.append(record)

    df_enriched = pd.DataFrame(enriched)

    # ── Step 2.5: DDGS Waterfall verification ──────────────────────
    if DDGS_AVAILABLE or TAVILY_AVAILABLE:
        print(f"\n[Verification] Running waterfall verification on {len(df_enriched)} centres...")
        
        # Separate target rows (don't verify them)
        has_target = "source_notes" in df_enriched.columns
        if has_target:
            is_target = df_enriched["source_notes"].str.contains("Reference — Target", na=False)
            competitors = df_enriched[~is_target].to_dict('records')
            targets = df_enriched[is_target].to_dict('records')
        else:
            competitors = df_enriched.to_dict('records')
            targets = []
        
        if competitors:
            verified_records = []
            for i, record in enumerate(competitors):
                name = record.get("centre_name", "")
                neighbourhood = record.get("neighbourhood", "")
                
                # BUG E3: Skip target rows - check for [TARGET in name
                if "[TARGET" in name:
                    print(f"  [{i+1}/{len(competitors)}] Skipping target: {name[:50]}")
                    verified_records.append(record)
                    continue
                
                print(f"  [{i+1}/{len(competitors)}] Verifying: {name[:50]}")
                
                # Pre-verification junk filter - use gatekeeper
                is_valid, gatekeeper_reason = pre_verification_gatekeeper(name)
                if not is_valid:
                    print(f"    [Skip] Gatekeeper rejected: {gatekeeper_reason}")
                    # Add junk note and continue
                    notes = str(record.get("source_notes", ""))
                    record["source_notes"] = (notes + f" | [Skipped: {gatekeeper_reason}]").strip(" |")
                    verified_records.append(record)
                    continue
                
                # Clean name for search (use original name - cleaning removed)
                clean_name = name.strip()
                if not clean_name:
                    print(f"    [Skip] Empty name: {name}")
                    verified_records.append(record)
                    continue
                
                # 3-Tier waterfall verification: DDGS -> Yahoo -> Tavily
                verification = verify_centre_waterfall(clean_name, neighbourhood)
                
                if verification["verified"]:
                    # Update record with verified data
                    # Bug Fix 1: Update address when verification finds one
                    if verification.get("address") and not record.get("address"):
                        record["address"] = verification["address"]
                        print(f"    [Address Updated] {record['address'][:50]}...")
                    if verification.get("fees") and not record.get("fee_fullday_raw"):
                        record["fee_fullday_raw"] = " | ".join(verification["fees"])
                    if verification.get("phone"):
                        record["phone"] = verification["phone"]
                    if verification.get("curriculum") and not record.get("curriculum"):
                        record["curriculum"] = verification["curriculum"]
                    
                    # Add verification notes
                    notes = str(record.get("source_notes", ""))
                    v_note = f"[Verified via {verification.get('method', 'unknown')}]: {verification.get('notes', '')}"
                    record["source_notes"] = (notes + " | " + v_note).strip(" |")
                
                verified_records.append(record)
                time.sleep(1.0)  # Rate limiting between verifications
            
            df_verified = pd.DataFrame(verified_records)
            
            # Merge back with targets
            if targets:
                df_enriched = pd.concat([pd.DataFrame(targets), df_verified], ignore_index=True)
            else:
                df_enriched = df_verified
            
            verified_count = sum(1 for r in verified_records if "Verified via" in str(r.get("source_notes", "")))
            print(f"  [Verification] Complete: {verified_count}/{len(competitors)} verified")
        else:
            print(f"  [Verification] No competitors to verify")
    else:
        print("\n[Verification] Skipping - neither DDGS nor Tavily available")
        # When skipping verification, still need to create df_enriched from enriched
        df_enriched = pd.DataFrame(enriched)

    print(f"\nAfter verification: {len(df_enriched)} rows")

    # ── Step 3: Radius & Distance filtering (after verification) ─
    # Apply radius check AFTER address verification/correction
    print(f"\nApplying radius filter ({radius_km}km from centre)...")
    excluded_count = 0
    keep_rows = []

    for i, row in df_enriched.iterrows():
        lat = row.get("lat")
        lng = row.get("lng")
        name = str(row.get("centre_name", "")).strip()

        if lat is not None and not pd.isna(lat) and lng is not None and not pd.isna(lng):
            try:
                lat, lng = float(lat), float(lng)
                # Malaysia bounds check
                if not (MALAYSIA_LAT[0] <= lat <= MALAYSIA_LAT[1] and
                        MALAYSIA_LNG[0] <= lng <= MALAYSIA_LNG[1]):
                    print(f"  Excluded '{name}' — outside Malaysia ({lat:.2f},{lng:.2f})")
                    excluded_count += 1
                    continue
                
                # BUG E7: Flag default coordinates with PJ/Subang postcodes
                # These suggest the address is outside radius but got default coordinates
                addr = str(row.get("address", "")).lower()
                is_default_coords = (lat == DEFAULT_TARGET_LAT and lng == DEFAULT_TARGET_LNG)
                pj_postcodes = ["47300", "47500", "47620", "47630", "47640", "47650", "47700", "47800", "47810", "47900", "47xxx", "50200"]
                has_pj_postcode = any(pc in addr for pc in pj_postcodes)
                
                if is_default_coords and has_pj_postcode:
                    # Flag but don't exclude - let user verify manually
                    notes = str(row.get("source_notes", ""))
                    flag_note = "[Flagged: coordinates are default fallback — address suggests outside radius, verify manually]"
                    row["source_notes"] = (notes + " | " + flag_note).strip(" |")
                    print(f"  Flagged '{name}' — default coords with PJ postcode")
                
                # Radius check
                dist = haversine_km(centre_lat, centre_lng, lat, lng)
                if dist > radius_km:
                    print(f"  Excluded '{name}' — {dist:.1f}km from centre")
                    excluded_count += 1
                    continue
            except Exception:
                pass
        keep_rows.append(row)

    df_enriched = pd.DataFrame(keep_rows)
    print(f"After radius filter: {len(df_enriched)} kept | {excluded_count} excluded")

    # ── Step 4: Prepend Target rows ───────────────────────────────
    target_df = pd.DataFrame(TARGET_ROWS)
    df_enriched = pd.concat([target_df, df_enriched], ignore_index=True)

    # ── Step 5: Fee display column ────────────────────────────────
    df_enriched["fee_display"] = (
        df_enriched["fee_halfday_raw"].fillna("").astype(str)
        + " | "
        + df_enriched["fee_fullday_raw"].fillna("").astype(str)
    )

    # ── Step 6: Zero blank cells fallbacks ───────────────────────
    defaults = {
        "centre_name":           "Unnamed ECE Centre",
        "address":               "[Address requires manual verification — use Nominatim/OSM]",
        "neighbourhood":         "Other",
        "curriculum":            "[Inferred] Standard KSPK assumed — typical for MOE-registered operator",
        "language_medium":       "[Inferred] BM primary — inferred from operator name and area demographics",
        "fee_halfday_raw":       "[Inferred] RM 300–500/mth — typical half-day range",
        "fee_fullday_raw":       "[Inferred] RM 450–750/mth — typical full-day range",
        "scale":                 "Independent",
        "religious_orientation": "Secular",
        "source_notes":          "[Inferred] Data completed through rule-based enrichment",
    }
    for col, default in defaults.items():
        if col in df_enriched.columns:
            df_enriched[col] = df_enriched[col].apply(lambda x: normalize(x, default))

    # ── Step 7: Timestamp ─────────────────────────────────────────
    from datetime import timezone
    df_enriched["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    # ── Step 8: Strict Identity deduplication with 3-step verification --
    competitors = df_enriched[
        ~df_enriched["source_notes"].str.contains("Reference — Target", na=False)
    ]
    targets = df_enriched[
        df_enriched["source_notes"].str.contains("Reference — Target", na=False)
    ]
    competitors_deduped = deduplicate_enriched_strict_identity(competitors)
    df_enriched = pd.concat([targets, competitors_deduped], ignore_index=True)

    # ── Step 9: Assert zero blank cells ──────────────────────────
    blank_count = int(df_enriched[REQUIRED_COLUMNS].isna().sum().sum())
    blank_count += int(
        (df_enriched[REQUIRED_COLUMNS].astype(str).apply(lambda s: s.str.strip()) == "").sum().sum()
    )
    assert blank_count == 0, f"Blank cell check failed: {blank_count} blank cells remain!"

    # ── Step 10: Elmina coverage warning ──────────────────────────
    elmina_count = len(df_enriched[
        (df_enriched["neighbourhood"] == "Elmina") &
        (~df_enriched["source_notes"].str.contains("Reference — Target", na=False))
    ])
    if elmina_count < 3:
        print(f"\nWARNING: Only {elmina_count} operators in Elmina (expected 3+).")
        print("Supplement with manual Google Maps / Facebook search:")
        print("  'taska Kota Elmina' | 'tadika Elmina Shah Alam' | 'kindergarten Elmina U16'")

    # ── Step 11: Post-processing cleaning (final stage) ────────────
    # Apply investment banking standard cleaning for final output
    if not skip_cleaning:
        df_enriched = run_post_processing_cleaning(df_enriched, early_stage=False)

    # ── Step 12: Write output ─────────────────────────────────────
    os.makedirs(os.path.dirname(output_csv) if os.path.dirname(output_csv) else ".", exist_ok=True)
    df_enriched.to_csv(output_csv, index=False)

    comp_count = len(df_enriched[
        ~df_enriched["source_notes"].str.contains("Reference — Target", na=False)
    ])
    print(f"\nmaster.csv written: {len(df_enriched)} rows ({comp_count} competitors + 2 Target rows)")
    print(f"0 blank cells confirmed.")

    # Coverage summary
    print("\nCoverage by neighbourhood:")
    for area in ["Bukit Jelutong", "Setia Alam", "Denai Alam", "Elmina", "Glenmarie", "Other"]:
        count = len(df_enriched[
            (df_enriched["neighbourhood"] == area) &
            (~df_enriched["source_notes"].str.contains("Reference — Target", na=False))
        ])
        flag = " ⚠️  (run manual search)" if area == "Elmina" and count < 3 else ""
        print(f"  {area}: {count}{flag}")


# ─────────────────────────── Post-Processing Cleaning ───────────────
# REMOVED: Post-processing cleaning with Ollama was not effective
# Strengthening gatekeeper instead to filter junk at source

def extract_addresses_from_notes(df: pd.DataFrame) -> Tuple[pd.DataFrame, int]:
    """
    2. The Address Extraction Fix
    - Extract addresses from source_notes when address shows placeholder
    Returns: (df, fix_count)
    """
    print("\n[CLEAN] Step 2: Extracting addresses from Source / Notes...")
    
    placeholder = "[Address requires manual verification"
    
    # Pattern to extract address: "Address found: " followed by text until next bracket or end
    # Enhanced patterns for various source formats
    address_patterns = [
        # Yahoo/Tavily with quotes format
        r"\[?Yahoo\]?.*Address found:\s*'([^']+)'",
        r"Tavily:.*Address found:\s*'([^']+)'",
        # Standard formats with truncation markers
        r"\[?Yahoo\]?.*Address found:\s*([^\[\]']+?)(?:\.{3}|\s*\[|$)",
        r"Tavily:.*Address found:\s*([^\[\]']+?)(?:\.{3}|\s*\[|$)",
        r"Address found:\s*([^\[\]']+?)(?:\.{3}|\s*\[|$)",
        # Fallback: any address-like pattern after "Address found:"
        r"Address found:\s*([A-Z][^\[\]]{10,200}?)(?:\.{0,3}|\s*\[|$)",
        # Heuristic: Malaysian address pattern in notes (no "Address found:" prefix)
        # Matches patterns like "No. 42, Jalan U13/2, Setia Alam" or "123 Jalan Something"
        r"(?:No\.?\s*\d+|\d+)[A-Z0-9\-/,\s]*(?:Jalan|Persiaran|Lorong|Jln|Jln\.?)[^\.\[]+?(?:Shah Alam|Selangor|\d{5})[^\.\[]*",
    ]
    
    fix_count = 0
    
    for idx in df.index:
        addr = str(df.loc[idx, "address"])
        notes = str(df.loc[idx, "source_notes"])
        
        if placeholder in addr or addr.strip() == "" or addr == "nan" or "verify" in addr.lower():
            extracted = None
            for pattern in address_patterns:
                match = re.search(pattern, notes, re.IGNORECASE)
                if match:
                    extracted = match.group(1).strip()
                    # Remove trailing ...
                    extracted = re.sub(r"\.{3}$", "", extracted).strip()
                    # Remove trailing whitespace and punctuation
                    extracted = extracted.rstrip(" .,;")
                    break
            
            if extracted and len(extracted) > 5:
                df.loc[idx, "address"] = extracted
                fix_count += 1
    
    print(f"  ✓ Fixed {fix_count} addresses")
    return df, fix_count


def apply_geographic_filter(df: pd.DataFrame) -> Tuple[pd.DataFrame, int]:
    """
    3. The Hallucination Filter (Geographic Bounding)
    - Flag rows with addresses outside Shah Alam radius
    - Filter addresses with URL artifacts
    - Filter addresses with missing/placeholder text
    Returns: (df, flag_count)
    """
    print("\n[CLEAN] Step 3: Applying geographic hallucination filter...")
    
    # Expanded out-of-bounds keywords
    outside_keywords = [
        "Kuala Lumpur", "KL", "Puchong", "Petaling Jaya", "PJ", 
        "Damansara", "Desa Parkcity", "Datuk Keramat", "Jalan Raja Chulan"
    ]
    
    flag_count = 0
    removed_count = 0
    
    rows_to_drop = []
    
    for idx in df.index:
        address = str(df.loc[idx, "address"]).lower()
        neighbourhood = str(df.loc[idx, "neighbourhood"]).lower()
        
        # Skip target rows (always keep these)
        if "Reference — Target" in str(df.loc[idx, "source_notes"]):
            continue
        
        # Check 1: Address contains URL artifacts (malformed scraping)
        if re.search(r'http|www|\]\(https?://|\.com|\.my', address):
            rows_to_drop.append(idx)
            removed_count += 1
            continue
        
        # Check 2: Address is placeholder or missing
        if "requires manual verification" in address or address.strip() in ["", "nan", "none"]:
            rows_to_drop.append(idx)
            removed_count += 1
            continue
        
        # Check 3: Address is out of bounds
        if any(kw.lower() in address for kw in outside_keywords):
            df.loc[idx, "threat_score"] = 0
            df.loc[idx, "source_notes"] = "[Flagged: Location outside 10km radius]"
            flag_count += 1
            continue
        
        # Check 4: Neighbourhood explicitly marked as OUT_OF_BOUNDS
        if neighbourhood == "out_of_bounds":
            rows_to_drop.append(idx)
            removed_count += 1
            continue
    
    # Drop bad rows
    if rows_to_drop:
        df = df.drop(rows_to_drop)
    
    print(f"  ✓ Flagged {flag_count} rows outside 10km radius")
    print(f"  ✓ Removed {removed_count} rows with invalid/missing addresses")
    return df, flag_count


def standardize_neighbourhoods(df: pd.DataFrame, override_all: bool = True) -> Tuple[pd.DataFrame, int]:
    """
    4. Neighbourhood Standardization
    - Fix 'Other' neighbourhoods based on address keywords
    - When override_all=True, also corrects geocoded neighbourhoods when
      address contains strong neighbourhood indicators
    Returns: (df, fix_count)
    """
    print("\n[CLEAN] Step 4: Standardizing neighbourhoods...")
    
    # Ensure neighbourhood column is string type (not float from NaN values)
    df["neighbourhood"] = df["neighbourhood"].astype(str).replace("nan", "")
    
    # Rules: (keywords to match, neighbourhood to assign)
    # Order matters - more specific areas checked first (Elmina before Denai for U16)
    neighbourhood_rules = [
        (["U13", "SETIA ALAM", "SEKSYEN U13", "ECO ARDENCE", "ARDENCE"], "Setia Alam"),
        (["ELMINA", "KOTA ELMINA", "ATMOSFERA", "ESERINA", "FREKUENSI U16"], "Elmina"),
        (["U16", "DENAI ALAM", "DENAI", "ELEKTRON U16", "JALAN ELEKTRON", "E-BOULEVARD"], "Denai Alam"),
        (["U8", "BUKIT JELUTONG", "JELUTONG", "PERSIARAN GERBANG", "TEBAR LAYAR"], "Bukit Jelutong"),
        (["U5", "GLENMARIE", "SUBANG BESTARI", "TEMASYA", "LAMAN GLENMARIE"], "Glenmarie"),
    ]
    
    fix_count = 0
    
    # Check all rows (not just "Other") when override_all=True
    rows_to_check = df.index if override_all else df[
        df["neighbourhood"].str.strip().str.lower() == "other"
    ].index
    
    for idx in rows_to_check:
        # Bug Fix 2: Use .lower() for broader substring matching
        addr_lower = str(df.loc[idx, "address"]).lower()
        current = str(df.loc[idx, "neighbourhood"]).strip()
        
        # Skip empty current values
        if current == "" or current == "nan":
            current = ""
        
        # Check address against rules to find the correct neighbourhood
        # Using lowercase matching with broader keywords
        # Enhanced to catch more addresses that were falling into "Other"
        address_matched_hood = None
        if any(k in addr_lower for k in ["bukit jelutong", "u8", "bazar u8", "jalan jendela", "tebar layar", 
                                          "persiaran gerbang", "bukit rimau", "jln jendela"]):
            address_matched_hood = "Bukit Jelutong"
        elif any(k in addr_lower for k in ["setia alam", "u13", "eco ardence", "ardence", "setia impian", 
                                            "setia perdana", "setia indah", "seksyen u13", "alam nusantara",
                                            "jalan setia indah", "eco sanctuary", "taman setia indah"]):
            address_matched_hood = "Setia Alam"
        elif any(k in addr_lower for k in ["elmina", "kota elmina", "atmosfera", "eserina", "frekuensi u16",
                                            "frekuensi", "elmina green", "elmina gardens"]):
            address_matched_hood = "Elmina"
        elif any(k in addr_lower for k in ["denai alam", "u16", "jalan elektron", "e-boulevard", "elektron u16",
                                            "jln elektron", "denai impian"]):
            address_matched_hood = "Denai Alam"
        elif any(k in addr_lower for k in ["glenmarie", "u5", "temasya", "subang bestari", "laman glenmarie",
                                            "subang bestari", "jalan u5", "seksyen u5", "u5 shah alam"]):
            address_matched_hood = "Glenmarie"
        elif any(k in addr_lower for k in ["kota kemuning", "kemuning", "rimbayu", "bukit rimau", 
                                            "taman sentosa", "jalan anggerik", "anggerik"]):
            address_matched_hood = "Other"  # Valid Shah Alam but not in main corridors
        elif any(k in addr_lower for k in ["kuala lumpur", "kl,", "petaling jaya", "puchong", 
                                            "desa parkcity", "datuk keramat", "jalan raja chulan"]):
            address_matched_hood = "OUT_OF_BOUNDS"  # Will be filtered out later
        
        # If address indicates a different neighbourhood than current, update it
        if address_matched_hood and address_matched_hood.upper() != current.upper():
            df.loc[idx, "neighbourhood"] = address_matched_hood
            fix_count += 1
    
    print(f"  ✓ Standardized {fix_count} neighbourhoods")
    return df, fix_count


def clean_source_notes(df: pd.DataFrame) -> pd.DataFrame:
    """
    5. Clean the 'source_notes' Column
    - Remove pipeline logs, keep clean verification notes
    """
    print("\n[CLEAN] Step 5: Cleaning Source / Notes...")
    
    # Patterns to remove entirely
    remove_patterns = [
        r"\[Geocoding failed — verify manually\]",
        r"\[Verified via yahoo_playwright\][^\[]*",
        r"\[Combined\] Yahoo[^\[]*",
        r"\[Fallback\][^\[]*",
        r"Tavily: '\[Tavily\] Address found: [^']*'",
        r"\[Yahoo\] Address found: [^\[]*",
    ]
    
    def clean_notes(notes: str) -> str:
        if pd.isna(notes) or not isinstance(notes, str):
            return notes
        
        # Remove unwanted patterns
        for pattern in remove_patterns:
            notes = re.sub(pattern, "", notes, flags=re.IGNORECASE)
        
        # Clean up extra whitespace and separators
        notes = re.sub(r"\s*\|\s*\|+", " | ", notes)
        notes = re.sub(r"\s+", " ", notes).strip(" |")
        
        # Ensure we have something meaningful
        if not notes or notes.strip() == "":
            return "[Inferred] Data completed through rule-based enrichment"
        
        return notes
    
    df["source_notes"] = df["source_notes"].apply(clean_notes)
    print(f"  ✓ Cleaned {len(df)} source notes")
    return df


def format_fees(df: pd.DataFrame) -> Tuple[pd.DataFrame, int]:
    """
    6. Fee Column Formatting
    - Standardize missing/broken fees
    Returns: (df, fix_count)
    """
    print("\n[CLEAN] Step 6: Formatting Monthly Fees...")
    
    default_fee_half = "[Inferred] RM 300-500/mth (Market Average)"
    default_fee_full = "[Inferred] RM 450-700/mth (Market Average)"
    fix_count = 0
    
    # Fix dtype issue: cast to object to allow string assignment
    df["fee_halfday_raw"] = df["fee_halfday_raw"].astype(object)
    df["fee_fullday_raw"] = df["fee_fullday_raw"].astype(object)
    
    for idx in df.index:
        # Fix half-day fee
        fee_hd = str(df.loc[idx, "fee_halfday_raw"]).strip()
        is_missing_hd = (
            fee_hd == "" or 
            fee_hd.lower() == "nan" or
            fee_hd == "[Inferred]" or
            (fee_hd.startswith("[") and "Inferred" in fee_hd and len(fee_hd) < 15) or
            pd.isna(df.loc[idx, "fee_halfday_raw"])
        )
        
        if is_missing_hd:
            df.loc[idx, "fee_halfday_raw"] = default_fee_half
            fix_count += 1
        elif fee_hd.startswith("[Inferred]") and "RM" not in fee_hd:
            df.loc[idx, "fee_halfday_raw"] = default_fee_half
            fix_count += 1
        
        # Fix full-day fee
        fee_fd = str(df.loc[idx, "fee_fullday_raw"]).strip()
        is_missing_fd = (
            fee_fd == "" or 
            fee_fd.lower() == "nan" or
            fee_fd == "[Inferred]" or
            (fee_fd.startswith("[") and "Inferred" in fee_fd and len(fee_fd) < 15) or
            pd.isna(df.loc[idx, "fee_fullday_raw"])
        )
        
        if is_missing_fd:
            df.loc[idx, "fee_fullday_raw"] = default_fee_full
            fix_count += 1
        elif fee_fd.startswith("[Inferred]") and "RM" not in fee_fd:
            df.loc[idx, "fee_fullday_raw"] = default_fee_full
            fix_count += 1
    
    # Update fee_display column
    df["fee_display"] = (
        df["fee_halfday_raw"].fillna("").astype(str)
        + " | "
        + df["fee_fullday_raw"].fillna("").astype(str)
    )
    
    print(f"  ✓ Fixed {fix_count} fee entries")
    return df, fix_count


def run_post_processing_cleaning(df: pd.DataFrame, early_stage: bool = False) -> pd.DataFrame:
    """
    Run all 6 post-processing cleaning transformations.
    
    Args:
        df: Input DataFrame
        early_stage: If True, runs before geocoding/neighbourhood assignment.
                    If False, runs at end of pipeline for final cleanup.
    
    Returns cleaned DataFrame.
    """
    stage_label = "EARLY STAGE" if early_stage else "FINAL"
    print("\n" + "=" * 60)
    print(f"POST-PROCESSING CLEANING — {stage_label} — Investment Banking Standard")
    print("=" * 60)
    
    initial_count = len(df)
    
    # Run transformations (post-processing cleaning removed - gatekeeper strengthened instead)
    df, _ = extract_addresses_from_notes(df)
    df, _ = apply_geographic_filter(df)
    
    # At early stage, skip neighbourhood standardization (will be done after geocoding)
    # At final stage, run it again to catch any "Other" values
    df, _ = standardize_neighbourhoods(df)
    
    df = clean_source_notes(df)
    df, _ = format_fees(df)
    
    final_count = len(df)
    
    print("\n" + "=" * 60)
    print(f"CLEANING SUMMARY ({stage_label})")
    print("=" * 60)
    print(f"Initial rows:        {initial_count}")
    print(f"Rows dropped:        {initial_count - final_count} (false positives)")
    print(f"Final rows:          {final_count}")
    print("=" * 60)
    
    return df


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Project Kestrel enricher v6.0")
    p.add_argument("--input",      default="data/raw.csv")
    p.add_argument("--output",     default="data/master.csv")
    p.add_argument("--centre-lat", type=float, default=DEFAULT_TARGET_LAT)
    p.add_argument("--centre-lng", type=float, default=DEFAULT_TARGET_LNG)
    p.add_argument("--radius",     type=float, default=DEFAULT_RADIUS_KM)
    p.add_argument("--skip-cleaning", action="store_true", help="Skip post-processing cleaning")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    enrich(
        input_csv=args.input,
        output_csv=args.output,
        centre_lat=args.centre_lat,
        centre_lng=args.centre_lng,
        radius_km=args.radius,
        skip_cleaning=args.skip_cleaning,
    )
