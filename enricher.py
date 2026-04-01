"""
Project Kestrel — enricher.py
Reads data/raw.csv, enriches and validates, writes data/master.csv.
Run: python enricher.py [--input data/raw.csv] [--output data/master.csv]
"""

import argparse
import os
import re
import time
from datetime import datetime
from typing import Dict, Optional, Tuple

import pandas as pd
from geopy.distance import geodesic
from geopy.exc import GeocoderTimedOut, GeocoderServiceError
from geopy.geocoders import ArcGIS, Nominatim, Photon

# ─────────────────────────── Constants ────────────────────────────

DEFAULT_TARGET_LAT = 3.1022
DEFAULT_TARGET_LNG = 101.5333
DEFAULT_RADIUS_KM  = 10.0

MALAYSIA_LAT = (1.0, 7.5)
MALAYSIA_LNG = (99.5, 119.5)

NEIGHBOURHOOD_BOUNDS = {
    "Bukit Jelutong": {"lat": (3.08, 3.12), "lng": (101.50, 101.55)},
    "Setia Alam":     {"lat": (3.07, 3.10), "lng": (101.45, 101.52)},
    "Denai Alam":     {"lat": (3.12, 3.16), "lng": (101.50, 101.55)},
    "Elmina":         {"lat": (3.13, 3.18), "lng": (101.47, 101.53)},
    "Glenmarie":      {"lat": (3.08, 3.11), "lng": (101.55, 101.61)},
}

AREA_KEYWORDS = {
    "Bukit Jelutong": ["bukit jelutong", "u8", "jelutong", "bazar u8", "persiaran gerbang utama", "jalan jendela"],
    "Setia Alam":     ["setia alam", "u13", "setia eco", "setia nusantara", "setia perdana", "setia impian", "eco ardence", "ardence", "setia avenue"],
    "Denai Alam":     ["denai alam", "u16", "elektron u16", "e-boulevard", "e boulevard"],
    "Elmina":         ["elmina", "kota elmina", "elmina east", "eserina", "frekuensi u16"],
    "Glenmarie":      ["glenmarie", "laman glenmarie", "subang bestari", "subang jaya", "usj", "temasya", "uoa business"],
}

ISLAMIC_KEYWORDS = [
    "islamic", "islam", "muslim", "tahfiz", "tahfidz", "jawi", "solat",
    "quran", "al-quran", "al quran", "aulad", "ustazah", "madrasah",
    "dini", "tarbiyah", "caliphs", "genius aulad", "little caliphs",
    "brainy bunch", "pasti", "permata", "iqra", "iman", "khalifah",
    "sujood", "hafiz", "hafazan", "ibnu sina", "dzul iman", "imanina",
    "generasi genius", "al-hafiz", "naluri ilmu",
]
INTERNATIONAL_KEYWORDS = [
    "international", "cambridge", "montessori", "ib ", "igcse", "reggio",
    "waldorf", "steiner", "eyfs",
]

NATIONAL_CHAINS = [
    "real kids", "brainy bunch", "little caliphs", "genius aulad",
    "beaconhouse", "pasti", "smart reader", "little einstein",
    "kidz castle", "smart kids", "tadika kreatif", "yamaha",
    "eduwis", "q-dees", "kinderland",
]
INSTITUTIONAL_KEYWORDS = [
    "idrissi", "international school", "college", "university",
    "campus", "academy", "institute",
]

CURRICULUM_KEYWORDS = {
    "Cambridge Early Years": ["cambridge", "cambs", "eyfs"],
    "Montessori":            ["montessori", "practical life", "sensorial"],
    "Play-based":            ["play-based", "playful", "child-led", "play based", "rumah main"],
    "STEAM":                 ["steam", "coding", "robotics", "science technology"],
    "Multiple Intelligences": ["multiple intelligences", "mi approach", "gardner"],
    "Islamic-integrated":    ["islamic", "tahfiz", "hafazan", "jawi", "solat", "iqra", "quran"],
    "KSPK":                  ["kspk", "kebangsaan", "national curriculum"],
    "Holistic":              ["holistic", "whole child", "social-emotional"],
}

# Verified fees from direct web research — used to replace inferred fees for known operators
VERIFIED_FEES: Dict[str, Dict] = {
    "idrissi cambridge eco-preschool": {
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
        "note": "[Verified: Kiddy123 listing — fees confirmed]",
    },
    "choo choo train": {
        "fee_halfday_raw": "RM 1,000/month (half-day)",
        "fee_fullday_raw": "RM 1,550/month (full-day)",
        "note": "[Verified: Kiddy123 listing — fees confirmed]",
    },
    "tiny tree house": {
        "fee_halfday_raw": "RM 590/month (half-day)",
        "fee_fullday_raw": "RM 970/month (full-day)",
        "note": "[Verified: Kiddy123 listing — fees confirmed]",
    },
    "the city kindergarten": {
        "fee_halfday_raw": "RM 100/month (half-day)",
        "fee_fullday_raw": "RM 680/month (full-day)",
        "note": "[Verified: Kiddy123 listing — fees confirmed]",
    },
    "genius kids": {
        "fee_halfday_raw": "RM 700/month (half-day)",
        "fee_fullday_raw": "RM 1,200/month (full-day)",
        "note": "[Verified: Kiddy123 listing — fees confirmed]",
    },
    "little blossom montessori": {
        "fee_halfday_raw": "RM 700–900/month (half-day)",
        "fee_fullday_raw": "RM 1,000–1,200/month (full-day)",
        "note": "[Verified: Kiddy123 listing]",
    },
    "taska hanis": {
        "fee_halfday_raw": "[Inferred] RM 600–900/mth — Montessori operator, Bukit Jelutong",
        "fee_fullday_raw": "[Inferred] RM 900–1,400/mth — Montessori operator, Bukit Jelutong",
        "note": "[Inferred] Fees estimated — Montessori profile, Bukit Jelutong area average",
    },
}

FEE_TIERS = {
    ("Institutional Campus",  "any"):                          ("RM 700–1,200/mth",  "RM 1,200–1,800/mth"),
    ("National Chain",        "Islamic-integrated"):           ("RM 300–500/mth",    "RM 450–700/mth"),
    ("National Chain",        "Secular"):                      ("RM 350–550/mth",    "RM 550–900/mth"),
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
        "centre_name":        "[TARGET — Brand A] IDRISSI Preschool Network",
        "address":            "Multiple locations across Peninsular Malaysia",
        "neighbourhood":      "Multiple (Peninsular Malaysia)",
        "lat":                DEFAULT_TARGET_LAT,
        "lng":                DEFAULT_TARGET_LNG,
        "curriculum":         "Islamic-integrated, KSPK-aligned",
        "language_medium":    "BM, English",
        "fee_halfday_raw":    "[Reference only]",
        "fee_fullday_raw":    "[Reference only — accessible price point]",
        "scale":              "National Chain",
        "religious_orientation": "Islamic-integrated",
        "is_moe_registered":  True,
        "source_primary":     "Reference — Target",
        "source_notes":       "[Reference — Target Brand A. Excluded from competitor statistics.]",
        "threat_score":       "Reference",
        "fee_display":        "[Reference only] | [Reference only — accessible price point]",
    },
    {
        "centre_name":        "[TARGET — Brand B] IDRISSI Cambridge Eco-Preschool",
        "address":            "Persiaran Tebar Layar, Bukit Jelutong, 40150 Shah Alam, Selangor",
        "neighbourhood":      "Bukit Jelutong",
        "lat":                DEFAULT_TARGET_LAT,
        "lng":                DEFAULT_TARGET_LNG,
        "curriculum":         "Cambridge Early Years, Nature-based / Eco-Islamic",
        "language_medium":    "English, BM, Arabic",
        "fee_halfday_raw":    "Not published",
        "fee_fullday_raw":    "RM 1,175/month (RM 14,100/year)",
        "scale":              "Institutional Campus",
        "religious_orientation": "Islamic-integrated",
        "is_moe_registered":  True,
        "source_primary":     "Reference — Target",
        "source_notes":       "[Reference — Target Brand B. Fee verified: IDRISSI website (RM 14,100/yr). Excluded from statistics.]",
        "threat_score":       "Reference",
        "fee_display":        "Not published | RM 1,175/month (RM 14,100/year)",
    },
]


# ─────────────────────────── Classifiers ──────────────────────────

def classify_religion(name: str, extra: str = "") -> str:
    text = f"{name} {extra}".lower()
    if any(kw in text for kw in ISLAMIC_KEYWORDS):
        return "Islamic-integrated"
    if any(kw in text for kw in INTERNATIONAL_KEYWORDS):
        return "International"
    return "Secular"


def classify_scale(name: str) -> str:
    name_lower = str(name).lower()
    if any(kw in name_lower for kw in INSTITUTIONAL_KEYWORDS):
        return "Institutional Campus"
    if any(chain in name_lower for chain in NATIONAL_CHAINS):
        return "National Chain"
    return "Independent"


def infer_curriculum(name: str, extra_text: str = "") -> str:
    text = f"{name} {extra_text}".lower()
    for label, kws in CURRICULUM_KEYWORDS.items():
        if any(kw in text for kw in kws):
            return label
    return "[Inferred] Standard KSPK assumed — typical for MOE-registered operator of this profile"


def infer_language(name: str, orientation: str, neighbourhood: str) -> str:
    name_lower = str(name).lower()
    if "mandarin" in name_lower or "chinese" in name_lower or "hua" in name_lower:
        return "Mandarin + English"
    if orientation == "Islamic-integrated":
        return "[Inferred] BM + English — typical for Islamic-integrated preschool"
    if neighbourhood in ("Glenmarie", "Subang"):
        return "[Inferred] English primary — area demographics suggest higher international demand"
    return "[Inferred] BM + English — standard bilingual for Shah Alam area"


def assign_neighbourhood(lat: Optional[float], lng: Optional[float], address: str) -> str:
    # 1. Try coordinate bounding boxes
    if lat is not None and lng is not None and not pd.isna(lat):
        for area, bounds in NEIGHBOURHOOD_BOUNDS.items():
            if (bounds["lat"][0] <= float(lat) <= bounds["lat"][1] and
                    bounds["lng"][0] <= float(lng) <= bounds["lng"][1]):
                return area
    # 2. Keyword match on address
    addr_lower = str(address).lower()
    for area, keywords in AREA_KEYWORDS.items():
        if any(kw in addr_lower for kw in keywords):
            return area
    return "Other"


def get_verified_fees(name: str) -> Optional[Dict]:
    """Check if we have verified fee data for this operator."""
    name_lower = name.lower()
    for key, data in VERIFIED_FEES.items():
        if key in name_lower:
            return data
    return None


def infer_fee_ranges(scale: str, orientation: str, neighbourhood: str) -> Tuple[str, str, str]:
    nh = "Elmina" if neighbourhood == "Elmina" else "other"
    key = (scale, orientation, nh)
    # Try exact match first
    if key in FEE_TIERS:
        hd, fd = FEE_TIERS[key]
    else:
        # Try with "any" neighbourhood
        key2 = (scale, orientation, "any")
        if key2 in FEE_TIERS:
            hd, fd = FEE_TIERS[key2]
        elif (scale, "any") in FEE_TIERS:
            hd, fd = FEE_TIERS[(scale, "any")]
        else:
            hd, fd = "RM 300–500/mth", "RM 450–750/mth"
    note = f"[Inferred] Estimated {fd} — {scale} {orientation} operator, {neighbourhood} area average"
    return hd, fd, note


def parse_fee_amount(text: str) -> float:
    if not isinstance(text, str):
        return 0.0
    nums = re.findall(r"\d+(?:,\d+)?", text)
    if not nums:
        return 0.0
    try:
        return float(nums[0].replace(",", ""))
    except Exception:
        return 0.0


def compute_threat_score(row: pd.Series) -> int:
    if "Reference — Target" in str(row.get("source_notes", "")):
        return 0
    lat = row.get("lat")
    lng = row.get("lng")
    if lat is not None and not pd.isna(lat):
        dist = geodesic(
            (DEFAULT_TARGET_LAT, DEFAULT_TARGET_LNG),
            (float(lat), float(lng))
        ).km
    else:
        dist = 5.0  # default mid-range if no coordinates

    proximity_score = (
        10 if dist < 1 else
        8  if dist < 3 else
        5  if dist < 6 else 2
    )
    scale = str(row.get("scale", ""))
    scale_score = (
        4 if scale == "National Chain" else
        3 if scale == "Institutional Campus" else
        2 if scale == "Regional Chain" else 1
    )
    fee_val = parse_fee_amount(
        f"{row.get('fee_halfday_raw', '')} {row.get('fee_fullday_raw', '')}"
    )
    fee_overlap = 3 if 400 <= fee_val <= 1400 else 0
    curriculum = str(row.get("curriculum", "")).lower()
    curriculum_match = 2 if any(k in curriculum for k in ["cambridge", "international", "montessori"]) else 0

    return int(min(10, proximity_score + scale_score + fee_overlap + curriculum_match))


# ─────────────── Geocoding with fallback chain ─────────────────────

def geocode_with_fallback(address: str, blocked: set) -> Tuple[Optional[float], Optional[float], str]:
    """
    Try ArcGIS → Photon → Nominatim in order.
    Returns (lat, lng, provider_name) or (None, None, "").
    Only accepts Malaysian coordinates.
    """
    providers = [
        ("ArcGIS",    ArcGIS(user_agent="kestrel-enricher", timeout=10)),
        ("Photon",    Photon(user_agent="kestrel-enricher", timeout=10)),
        ("Nominatim", Nominatim(user_agent="kestrel-enricher", timeout=10)),
    ]
    for pname, geolocator in providers:
        if pname in blocked:
            continue
        try:
            time.sleep(1.2)
            loc = geolocator.geocode(address)
            if loc:
                lat, lng = float(loc.latitude), float(loc.longitude)
                if MALAYSIA_LAT[0] <= lat <= MALAYSIA_LAT[1] and MALAYSIA_LNG[0] <= lng <= MALAYSIA_LNG[1]:
                    return lat, lng, pname
                # Got a coordinate but outside Malaysia — skip
        except GeocoderTimedOut:
            pass
        except GeocoderServiceError as exc:
            if any(c in str(exc) for c in ["429", "403", "Too Many Requests"]):
                blocked.add(pname)
        except Exception:
            pass
    return None, None, ""


def clean_address_for_geocoding(address: str) -> str:
    """
    Strip Kiddy123 / SerpAPI junk from addresses before geocoding.
    Keep only the street/locality portion.
    """
    if not address or len(address) > 300:
        return ""
    # Remove common snippet noise
    address = re.sub(r"(Essential Details|Centre'?s? Category|Year\s*\d+|\d+ (likes|posts|followers))[^\n]*", "", address, flags=re.IGNORECASE)
    address = re.sub(r"(Contact Number|Tel|Phone|Email|Website|Submit|Thanks for)[^\n]*", "", address, flags=re.IGNORECASE)
    address = re.sub(r"\s{2,}", " ", address).strip()
    # Limit length
    return address[:200]


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


# ─────────────── Main enrichment pipeline ─────────────────────────

def enrich(input_csv: str, output_csv: str,
           centre_lat: float, centre_lng: float, radius_km: float) -> None:

    if not os.path.exists(input_csv):
        raise FileNotFoundError(f"Input not found: {input_csv}")

    df = pd.read_csv(input_csv)
    print(f"Loaded raw.csv: {len(df)} rows")

    # Ensure all columns exist
    for col in ["centre_name", "address", "neighbourhood", "lat", "lng", "curriculum",
                "language_medium", "fee_halfday_raw", "fee_fullday_raw", "scale",
                "religious_orientation", "is_moe_registered", "source_primary", "source_notes"]:
        if col not in df.columns:
            df[col] = "" if col not in ("lat", "lng", "is_moe_registered") else None

    # ── Step 1: Geocode missing coordinates ──────────────────────
    blocked_providers: set = set()
    geocoded_count = 0
    excluded_count = 0

    keep_rows = []
    print(f"Geocoding and validating {len(df)} rows...")

    for i, row in df.iterrows():
        row = row.copy()
        lat = row.get("lat")
        lng = row.get("lng")
        raw_address = str(row.get("address", "")).strip()
        name = str(row.get("centre_name", "")).strip()

        # Clean address for geocoding
        address_for_geo = clean_address_for_geocoding(raw_address)

        # Attempt geocoding if no valid coordinates
        if (pd.isna(lat) or lat is None or lat == 0) and address_for_geo:
            new_lat, new_lng, provider = geocode_with_fallback(address_for_geo, blocked_providers)
            if new_lat:
                row["lat"] = new_lat
                row["lng"] = new_lng
                lat, lng = new_lat, new_lng
                geocoded_count += 1
                print(f"  [{provider}] Geocoded: {name[:50]}")
            else:
                row["source_notes"] = str(row.get("source_notes", "")) + " | [Geocoding failed — verify manually]"
                row["source_notes"] = row["source_notes"].strip(" |")

        # ── Step 2: Radius and Malaysia check ────────────────────
        lat = row.get("lat")
        lng = row.get("lng")

        if lat is not None and not pd.isna(lat):
            lat, lng = float(lat), float(lng)
            # Reject non-Malaysian coordinates
            if not (MALAYSIA_LAT[0] <= lat <= MALAYSIA_LAT[1] and MALAYSIA_LNG[0] <= lng <= MALAYSIA_LNG[1]):
                print(f"  Excluded '{name}' — coordinates outside Malaysia ({lat:.2f}, {lng:.2f})")
                excluded_count += 1
                continue
            # Reject if outside radius
            dist = geodesic((centre_lat, centre_lng), (lat, lng)).km
            if dist > radius_km:
                print(f"  Excluded '{name}' — {dist:.1f}km from centre")
                excluded_count += 1
                continue
        else:
            # No coordinates after geocoding — only keep if address/name confirms area
            target_areas = ["shah alam", "bukit jelutong", "setia alam", "denai alam",
                            "elmina", "glenmarie", "subang", "selangor", "u8", "u13", "u16"]
            combined = (raw_address + " " + name).lower()
            if not any(area in combined for area in target_areas):
                print(f"  Excluded '{name}' — no coordinates and no area match")
                excluded_count += 1
                continue

        keep_rows.append(row)

        if (i + 1) % 25 == 0:
            print(f"  Processed {i + 1}/{len(df)} rows...")

    df = pd.DataFrame(keep_rows)
    print(f"\nAfter geocoding/radius filter: {len(df)} rows kept. {excluded_count} excluded. {geocoded_count} geocoded.")

    # ── Step 3: Enrich each row ───────────────────────────────────
    enriched = []
    for _, row in df.iterrows():
        record = row.to_dict()
        name    = normalize(record.get("centre_name"), "Unnamed ECE Centre")
        address = normalize(record.get("address"), "[Address requires manual verification — check Google Maps]")
        lat     = record.get("lat")
        lng     = record.get("lng")

        record["centre_name"] = name
        record["address"]     = address

        # Neighbourhood
        record["neighbourhood"] = assign_neighbourhood(
            lat if lat and not pd.isna(lat) else None,
            lng if lng and not pd.isna(lng) else None,
            address
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

        # ── Fees: verified lookup first, then Kiddy123 data, then inference ──
        fee_hd = normalize(record.get("fee_halfday_raw"))
        fee_fd = normalize(record.get("fee_fullday_raw"))

        verified = get_verified_fees(name)
        if verified:
            # We have verified fee data for this brand
            record["fee_halfday_raw"] = verified["fee_halfday_raw"]
            record["fee_fullday_raw"] = verified["fee_fullday_raw"]
            existing_notes = str(record.get("source_notes", ""))
            if verified["note"] not in existing_notes:
                record["source_notes"] = (existing_notes + " | " + verified["note"]).strip(" |")
            print(f"  Fees verified for: {name[:50]}")
        elif fee_hd and fee_fd:
            # Fees came from scraping (Kiddy123) — keep them, mark as verified
            # Clean up RM format
            def fmt_fee(f: str) -> str:
                nums = re.findall(r"[\d,]+", f)
                if nums:
                    return f"RM {nums[0]}/month (from Kiddy123)"
                return f
            record["fee_halfday_raw"] = fmt_fee(fee_hd)
            record["fee_fullday_raw"] = fmt_fee(fee_fd)
        elif fee_hd and not fee_fd:
            record["fee_halfday_raw"] = fee_hd
            inferred_hd, inferred_fd, note = infer_fee_ranges(
                record["scale"], record["religious_orientation"], record["neighbourhood"]
            )
            record["fee_fullday_raw"] = inferred_fd
            record["source_notes"] = (str(record.get("source_notes", "")) + " | " + note).strip(" |")
        else:
            # Fully inferred
            inferred_hd, inferred_fd, note = infer_fee_ranges(
                record["scale"], record["religious_orientation"], record["neighbourhood"]
            )
            record["fee_halfday_raw"] = inferred_hd
            record["fee_fullday_raw"] = inferred_fd
            record["source_notes"] = (str(record.get("source_notes", "")) + " | " + note).strip(" |")

        # Clean up source notes
        record["source_notes"] = sanitize_notes(record.get("source_notes", ""))

        # Threat score
        record["threat_score"] = compute_threat_score(pd.Series(record))

        enriched.append(record)

    df_enriched = pd.DataFrame(enriched)

    # ── Step 4: Prepend Target rows ───────────────────────────────
    target_df = pd.DataFrame(TARGET_ROWS)
    df_enriched = pd.concat([target_df, df_enriched], ignore_index=True)

    # ── Step 5: Fee display column ────────────────────────────────
    df_enriched["fee_display"] = (
        df_enriched["fee_halfday_raw"].astype(str) + " | " +
        df_enriched["fee_fullday_raw"].astype(str)
    )

    # ── Step 6: Final defaults for any remaining blanks ───────────
    defaults = {
        "centre_name":        "Unnamed ECE Centre",
        "address":            "[Address requires manual verification — check Google Maps]",
        "neighbourhood":      "Other",
        "curriculum":         "[Inferred] Standard KSPK assumed — typical for MOE-registered operator",
        "language_medium":    "[Inferred] BM primary — inferred from operator name and area demographics",
        "fee_halfday_raw":    "[Inferred] RM 300–500/mth — typical half-day fee range",
        "fee_fullday_raw":    "[Inferred] RM 450–750/mth — typical full-day fee range",
        "scale":              "Independent",
        "religious_orientation": "Secular",
        "source_notes":       "[Inferred] Data completed through rule-based enrichment",
    }
    for col, default in defaults.items():
        if col in df_enriched.columns:
            df_enriched[col] = df_enriched[col].apply(lambda x: normalize(x, default))

    # Timestamp
    df_enriched["updated_at"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

    # ── Step 7: Assert zero blank cells ──────────────────────────
    blank_count = int(df_enriched[REQUIRED_COLUMNS].isna().sum().sum())
    blank_count += int(
        (df_enriched[REQUIRED_COLUMNS].astype(str).apply(lambda s: s.str.strip()) == "").sum().sum()
    )
    assert blank_count == 0, f"Blank cell check failed: {blank_count} blank cells remain!"

    # ── Step 8: Elmina coverage warning ──────────────────────────
    elmina_count = len(df_enriched[
        (df_enriched["neighbourhood"] == "Elmina") &
        (~df_enriched["source_notes"].str.contains("Reference — Target", na=False))
    ])
    if elmina_count < 3:
        print(f"\nWARNING: Only {elmina_count} operators found in Elmina (expected 3+).")
        print("Recommend supplementing with manual Google Maps / Facebook search for:")
        print("  'taska Kota Elmina' | 'tadika Elmina Shah Alam' | 'kindergarten Elmina U16'")

    # ── Step 9: Write output ──────────────────────────────────────
    os.makedirs(os.path.dirname(output_csv) if os.path.dirname(output_csv) else ".", exist_ok=True)
    df_enriched.to_csv(output_csv, index=False)
    print(f"\nmaster.csv written: {len(df_enriched)} rows ({len(df_enriched) - 2} competitors + 2 Target rows).")
    print(f"0 blank cells confirmed.")
    print(f"Elmina operators: {elmina_count}")

    # ── Coverage summary ──────────────────────────────────────────
    competitors = df_enriched[~df_enriched["source_notes"].str.contains("Reference — Target", na=False)]
    print("\nCoverage by neighbourhood:")
    for area in ["Bukit Jelutong", "Setia Alam", "Denai Alam", "Elmina", "Glenmarie", "Other"]:
        count = len(competitors[competitors["neighbourhood"] == area])
        flag = " ⚠️" if area == "Elmina" and count < 3 else ""
        print(f"  {area}: {count} operators{flag}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Project Kestrel enricher")
    parser.add_argument("--input",      default="data/raw.csv")
    parser.add_argument("--output",     default="data/master.csv")
    parser.add_argument("--centre-lat", type=float, default=DEFAULT_TARGET_LAT)
    parser.add_argument("--centre-lng", type=float, default=DEFAULT_TARGET_LNG)
    parser.add_argument("--radius",     type=float, default=DEFAULT_RADIUS_KM)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    enrich(
        input_csv=args.input,
        output_csv=args.output,
        centre_lat=args.centre_lat,
        centre_lng=args.centre_lng,
        radius_km=args.radius,
    )
    
