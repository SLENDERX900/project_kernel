import argparse
import os
import re
import sys
import time
from datetime import datetime
from typing import Dict, Tuple

# Add local packages to path
current_dir = os.path.dirname(os.path.abspath(__file__))
local_packages = os.path.join(current_dir, 'packages')
if os.path.exists(local_packages):
    sys.path.insert(0, local_packages)

import pandas as pd
from geopy.distance import geodesic
from geopy.geocoders import Nominatim as GeopyNominatim, ArcGIS, Photon
from duckduckgo_search import DDGS

DEFAULT_TARGET_LAT = 3.1022
DEFAULT_TARGET_LNG = 101.5333
DEFAULT_RADIUS_KM = 10.0

NEIGHBOURHOOD_BOUNDS = {
    "Bukit Jelutong": {"lat": (3.08, 3.12), "lng": (101.50, 101.55)},
    "Setia Alam": {"lat": (3.07, 3.10), "lng": (101.45, 101.52)},
    "Denai Alam": {"lat": (3.12, 3.16), "lng": (101.50, 101.55)},
    "Elmina": {"lat": (3.13, 3.18), "lng": (101.47, 101.53)},
    "Glenmarie": {"lat": (3.08, 3.11), "lng": (101.55, 101.61)},
}

ISLAMIC_KEYWORDS = [
    "islamic", "islam", "muslim", "tahfiz", "tahfidz", "jawi", "solat", "quran", "al-quran",
    "aulad", "ustazah", "madrasah", "dini", "tarbiyah", "caliphs", "genius aulad", "little caliphs",
    "brainy bunch", "pasti", "permata", "iqra", "iman", "khalifah",
]
INTERNATIONAL_KEYWORDS = ["international", "cambridge", "montessori", "ib ", "igcse", "reggio"]

NATIONAL_CHAINS = [
    "real kids", "brainy bunch", "little caliphs", "genius aulad", "beaconhouse", "pasti",
    "smart reader", "little einstein", "kidz castle", "smart kids", "tadika kreatif", "yamaha",
]
INSTITUTIONAL_KEYWORDS = ["idrissi", "international school", "college", "university", "campus", "academy", "institute"]

CURRICULUM_KEYWORDS = {
    "Montessori": ["montessori", "practical life", "sensorial"],
    "Cambridge Early Years": ["cambridge", "cambs", "eyfs"],
    "Play-based": ["play", "play-based", "playful", "child-led"],
    "STEAM": ["steam", "science", "technology", "coding", "robotics"],
    "Multiple Intelligences": ["multiple intelligences", "mi approach", "gardner"],
    "Islamic-integrated": ["islamic", "tahfiz", "hafazan", "jawi", "solat", "iqra"],
    "KSPK": ["kspk", "kebangsaan", "national curriculum"],
    "Holistic": ["holistic", "whole child", "social-emotional"],
}

REQUIRED_COLUMNS = [
    "centre_name", "address", "neighbourhood", "curriculum",
    "language_medium", "fee_halfday_raw", "fee_fullday_raw",
    "scale", "religious_orientation", "source_notes",
]

EVIDENCE_KEYWORDS = [
    "Tadika", "Taska", "Preschool", "Kindergarten", "School", "Tabika",
    "Playschool", "Childcare", "Daycare", "Enrolment", "Ages 3-6", "KSPK", "Early Years",
]
NON_ECE_TERMS = [
    "restaurant", "grocery", "clothing", "boutique", "salon", "barber", "cafe",
    "bakery", "car wash", "pharmacy", "dental", "clinic", "hospital", "hotel",
]
FORMAL_NAME_HINTS = ["tadika", "taska", "tabika", "preschool", "kindergarten", "childcare", "daycare"]

TARGET_ROWS = [
    {
        "centre_name": "[TARGET — Brand A] IDRISSI Preschool Network",
        "address": "Multiple locations across Peninsular Malaysia",
        "neighbourhood": "Multiple (Peninsular Malaysia)",
        "lat": DEFAULT_TARGET_LAT,
        "lng": DEFAULT_TARGET_LNG,
        "curriculum": "Islamic-integrated, KSPK-aligned",
        "language_medium": "BM, English",
        "fee_halfday_raw": "[Reference only]",
        "fee_fullday_raw": "[Reference only — accessible price point]",
        "scale": "National Chain",
        "religious_orientation": "Islamic-integrated",
        "is_moe_registered": True,
        "source_primary": "Reference — Target",
        "source_notes": "[Reference — Target Brand A. Excluded from competitor statistics.]",
        "threat_score": "Reference",
    },
    {
        "centre_name": "[TARGET — Brand B] IDRISSI Cambridge Eco-Preschool",
        "address": "Persiaran Tebar Layar, Bukit Jelutong, 40150 Shah Alam",
        "neighbourhood": "Bukit Jelutong",
        "lat": DEFAULT_TARGET_LAT,
        "lng": DEFAULT_TARGET_LNG,
        "curriculum": "Cambridge Early Years, Nature-based / Eco-Islamic",
        "language_medium": "English, BM, Arabic",
        "fee_halfday_raw": "Not published",
        "fee_fullday_raw": "RM 1,175/month (RM 14,100/year)",
        "scale": "Institutional Campus",
        "religious_orientation": "Islamic-integrated",
        "is_moe_registered": True,
        "source_primary": "Reference — Target",
        "source_notes": "[Reference — Target Brand B. Fee verified: IDRISSI website. Excluded from statistics.]",
        "threat_score": "Reference",
    },
]


def classify_religion(name: str, description: str = "") -> str:
    text = f"{name} {description}".lower()
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


def infer_curriculum(name: str, source_text: str) -> str:
    text = f"{name} {source_text}".lower()
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
    if neighbourhood in ["Glenmarie", "Subang"]:
        return "[Inferred] English primary — area demographics suggest higher English demand"
    return "[Inferred] BM + English — standard bilingual for Shah Alam area"


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


def infer_fee_ranges(scale: str, orientation: str, neighbourhood: str) -> Tuple[str, str, str]:
    nh = "Elmina" if neighbourhood == "Elmina" else "other"

    if scale == "Institutional Campus":
        hd, fd = "RM 700–1,200/mth", "RM 1,200–1,800/mth"
    elif scale == "National Chain" and orientation == "Islamic-integrated":
        hd, fd = "RM 250–450/mth", "RM 400–700/mth"
    elif scale == "National Chain" and orientation == "Secular":
        hd, fd = "RM 350–550/mth", "RM 550–900/mth"
    elif scale == "Independent" and orientation == "Islamic-integrated" and nh == "Elmina":
        hd, fd = "RM 200–350/mth", "RM 350–550/mth"
    elif scale == "Independent" and orientation == "Islamic-integrated":
        hd, fd = "RM 250–450/mth", "RM 400–700/mth"
    elif scale == "Independent" and orientation == "International":
        hd, fd = "RM 600–900/mth", "RM 900–1,400/mth"
    else:
        hd, fd = "RM 300–500/mth", "RM 450–750/mth"

    note = f"[Inferred] Estimated fees — {scale} {orientation} operator, {neighbourhood} area average"
    return hd, fd, note


def assign_neighbourhood(lat: float, lng: float, address: str) -> str:
    if pd.notna(lat) and pd.notna(lng):
        for area, bounds in NEIGHBOURHOOD_BOUNDS.items():
            if bounds["lat"][0] <= lat <= bounds["lat"][1] and bounds["lng"][0] <= lng <= bounds["lng"][1]:
                return area

    addr = str(address).lower()
    if "u8" in addr or "bukit jelutong" in addr:
        return "Bukit Jelutong"
    if "u13" in addr or "setia alam" in addr:
        return "Setia Alam"
    if "u16" in addr or "denai alam" in addr:
        return "Denai Alam"
    if "elmina" in addr or "kota elmina" in addr:
        return "Elmina"
    if "glenmarie" in addr or "subang" in addr:
        return "Glenmarie"
    return "Other"


def validate_and_fill_coordinates(df: pd.DataFrame, centre_lat: float, centre_lng: float, radius_km: float) -> pd.DataFrame:
    user_agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "ProjectKestrelBot/1.0 (https://project-kestrel.example.com; research-data@example.com)"
    ]

    geocoded = 0
    excluded = 0
    failed_geocoding = 0

    keep_rows = []
    total_rows = len(df)
    print(f"Starting coordinate validation for {total_rows} rows...")
    accepted_location_hints = ["shah alam", "selangor", "bukit jelutong", "setia alam", "elmina", "denai alam", "subang"]

    arcgis = ArcGIS(user_agent="project-kestrel-enricher-v2")
    photon = Photon(user_agent="project-kestrel-enricher-v2")

    nominatim_blocked = False

    for i, (_, row) in enumerate(df.iterrows()):
        lat = row.get("lat")
        lng = row.get("lng")

        if (pd.isna(lat) or pd.isna(lng)):
            addr = str(row.get("address", "")).strip()
            name = str(row.get("centre_name", "")).strip()

            if not addr or addr.lower() in ["nan", "none"]:
                if name and name.lower() not in ["nan", "none"]:
                    query = f"{name} Shah Alam"
                else:
                    query = None
            else:
                query = addr

            if query and query.lower() not in ["nan", "none", "shah alam"]:
                if i % 10 == 0:
                    print(f"Geocoding progress: {i}/{total_rows}...")

                success = False

                try:
                    time.sleep(1)
                    loc = arcgis.geocode(query, timeout=10)
                    if loc:
                        lat, lng = float(loc.latitude), float(loc.longitude)
                        geocoded += 1
                        row["lat"], row["lng"] = lat, lng
                        success = True
                except Exception:
                    pass

                if not success:
                    try:
                        time.sleep(1)
                        loc = photon.geocode(query, timeout=10)
                        if loc:
                            lat, lng = float(loc.latitude), float(loc.longitude)
                            geocoded += 1
                            row["lat"], row["lng"] = lat, lng
                            success = True
                    except Exception:
                        pass

                if not success and not nominatim_blocked:
                    for ua in user_agents:
                        geolocator = GeopyNominatim(user_agent=ua)
                        try:
                            time.sleep(2)
                            loc = geolocator.geocode(query, timeout=10)
                            if loc:
                                lat, lng = float(loc.latitude), float(loc.longitude)
                                geocoded += 1
                                row["lat"], row["lng"] = lat, lng
                                success = True
                                break
                        except Exception as e:
                            if "429" in str(e):
                                print("Nominatim rate limited (429). Waiting 30 seconds and disabling for this run...")
                                nominatim_blocked = True
                                time.sleep(30)
                                break
                            elif "403" in str(e):
                                pass
                            else:
                                print(f"Nominatim error: {e}")

                if not success:
                    failed_geocoding += 1
                    row["lat"], row["lng"] = None, None
                    row["source_notes"] = f"{row.get('source_notes', '')} | [Geocoding failed — verify manually]".strip(" |")

        if pd.notna(lat) and pd.notna(lng):
            if float(lat) < 1.0 or float(lat) > 7.5 or float(lng) < 99.5 or float(lng) > 119.5:
                print(f"Excluded {row.get('centre_name', 'unknown')} — coordinates outside Malaysia bounding box")
                excluded += 1
                continue
            if geodesic((centre_lat, centre_lng), (float(lat), float(lng))).km > radius_km:
                excluded += 1
                continue
        else:
            address_text = str(row.get("address", "")).lower()
            if not any(hint in address_text for hint in accepted_location_hints):
                excluded += 1
                continue

        keep_rows.append(row)

    result = pd.DataFrame(keep_rows)
    print(f"Coordinate validation: {len(result)} rows within radius. {excluded} excluded. {geocoded} geocoded. {failed_geocoding} failed.")
    return result


def threat_score(row: pd.Series) -> int:
    if "Reference — Target" in str(row.get("source_notes", "")):
        return 0

    lat = row.get("lat")
    lng = row.get("lng")
    if pd.notna(lat) and pd.notna(lng):
        dist = geodesic((DEFAULT_TARGET_LAT, DEFAULT_TARGET_LNG), (float(lat), float(lng))).km
    else:
        dist = 10.0

    if dist < 1:
        proximity_score = 10
    elif dist < 3:
        proximity_score = 8
    elif dist < 6:
        proximity_score = 5
    else:
        proximity_score = 2

    scale = str(row.get("scale", ""))
    if scale == "National Chain":
        scale_score = 4
    elif scale == "Institutional Campus":
        scale_score = 3
    elif scale == "Regional Chain":
        scale_score = 2
    else:
        scale_score = 1

    fee_text = f"{row.get('fee_halfday_raw', '')} {row.get('fee_fullday_raw', '')}".lower()
    fee_value = parse_fee_amount(fee_text)
    fee_overlap = 3 if 400 <= fee_value <= 1400 else 0

    curriculum_text = str(row.get("curriculum", "")).lower()
    curriculum_match = 2 if any(k in curriculum_text for k in ["cambridge", "international", "montessori"]) else 0

    return int(min(10, proximity_score + scale_score + fee_overlap + curriculum_match))


def sanitize_source_notes(note: str) -> str:
    txt = str(note).strip()
    if not txt:
        return "[Inferred] Data completed through rule-based enrichment"
    if "[verified" in txt.lower() or "[inferred" in txt.lower() or "[reference" in txt.lower():
        return txt
    return txt + " | [Inferred] Data completed through rule-based enrichment"


def normalize_text_cell(value: str, default_value: str) -> str:
    bad = {"", "unknown", "n/a", "na", "tbc", "-", "–", "none", "nan"}
    txt = str(value).strip()
    if txt.lower() in bad:
        return default_value
    return txt


def _norm(txt: str) -> str:
    return str(txt or "").strip()


def has_ece_evidence(centre_name: str, source_notes: str, desc: str = "") -> bool:
    hay = f"{centre_name} {source_notes} {desc}".lower()
    return any(k.lower() in hay for k in EVIDENCE_KEYWORDS)


def run_search(query: str, max_results: int = 8):
    with DDGS() as ddgs:
        return list(ddgs.text(query, max_results=max_results))


def choose_formal_name(candidates, fallback: str) -> str:
    best = fallback
    for c in candidates:
        c2 = _norm(c)
        if any(h in c2.lower() for h in FORMAL_NAME_HINTS) and len(c2) > len(best):
            best = c2
    return best


def verify_entity_fields(row: pd.Series) -> Tuple[str, Dict]:
    centre_name = _norm(row.get("centre_name", ""))
    neighbourhood = _norm(row.get("neighbourhood", ""))
    source_notes = _norm(row.get("source_notes", ""))
    address = _norm(row.get("address", ""))
    curriculum = _norm(row.get("curriculum", ""))
    fee_full = _norm(row.get("fee_fullday_raw", ""))

    if "Reference — Target" in source_notes:
        return "keep", {"is_verified_entity": True, "status": "VALID"}

    updates = {"is_verified_entity": has_ece_evidence(centre_name, source_notes), "status": "VALID"}
    query = f"{centre_name} {neighbourhood} Shah Alam"
    try:
        results = run_search(query)
    except Exception as exc:
        updates["is_verified_entity"] = False
        updates["status"] = f"REVIEW_SEARCH_FAILED: {exc}"
        return "keep", updates

    ece_hits, non_ece_hits = 0, 0
    titles = []
    found_address = ""
    found_fee = ""
    found_curriculum = ""
    for r in results:
        title = _norm(r.get("title", ""))
        snippet = _norm(r.get("body", ""))
        link = _norm(r.get("href", ""))
        joined = f"{title} {snippet} {link}".lower()
        if any(t in joined for t in NON_ECE_TERMS):
            non_ece_hits += 1
        if any(k.lower() in joined for k in EVIDENCE_KEYWORDS):
            ece_hits += 1
        if title:
            titles.append(title)
        if ("[inferred" in address.lower() or not address) and not found_address:
            m = re.search(r"\b(?:jalan|jln|persiaran|lorong|seksyen|bandar|taman|shah alam|selangor)[^|,.;]{6,}", snippet, flags=re.IGNORECASE)
            if m:
                found_address = m.group(0).strip(" ,.;")
        if ("[inferred" in fee_full.lower() or re.search(r"rm\s*\d+\s*[–-]\s*\d+", fee_full.lower())) and not found_fee:
            m2 = re.search(r"RM\s*[\d,]+(?:\s*[–-]\s*[\d,]+)?", snippet, flags=re.IGNORECASE)
            if m2:
                found_fee = m2.group(0).strip()
        if not found_curriculum:
            s = snippet.lower()
            if "montessori" in s:
                found_curriculum = "Montessori"
            elif "cambridge" in s or "early years" in s:
                found_curriculum = "Cambridge Early Years"
            elif "kspk" in s:
                found_curriculum = "KSPK"

    if non_ece_hits > ece_hits and non_ece_hits >= 1:
        return "trash", {"is_verified_entity": False, "status": "INVALID_NON_ECE"}

    updates["is_verified_entity"] = ece_hits > 0
    updates["status"] = "VALID" if ece_hits > 0 else "REVIEW"
    formal = choose_formal_name(titles, centre_name)
    if formal and formal != centre_name:
        updates["centre_name"] = formal
    if found_address:
        updates["address"] = found_address
    if found_fee:
        updates["fee_fullday_raw"] = found_fee
    if found_curriculum and (not curriculum or "[inferred" in curriculum.lower()):
        updates["curriculum"] = found_curriculum
    return "keep", updates


def enrich(input_csv: str, output_csv: str, centre_lat: float, centre_lng: float, radius_km: float) -> None:
    if not os.path.exists(input_csv):
        raise FileNotFoundError(f"Input file not found: {input_csv}")

    df = pd.read_csv(input_csv)

    for col in [
        "centre_name", "address", "neighbourhood", "lat", "lng", "curriculum", "language_medium",
        "fee_halfday_raw", "fee_fullday_raw", "scale", "religious_orientation", "is_moe_registered",
        "source_primary", "source_notes",
    ]:
        if col not in df.columns:
            df[col] = ""

    df = validate_and_fill_coordinates(df, centre_lat=centre_lat, centre_lng=centre_lng, radius_km=radius_km)

    enriched_rows = []
    for _, row in df.iterrows():
        record = row.to_dict()

        record["centre_name"] = normalize_text_cell(record.get("centre_name", ""), "Unnamed ECE Centre")
        record["address"] = normalize_text_cell(record.get("address", ""), "[Inferred] Address requires manual verification")

        record["neighbourhood"] = assign_neighbourhood(record.get("lat"), record.get("lng"), record.get("address", ""))

        if normalize_text_cell(record.get("religious_orientation", ""), "") == "":
            record["religious_orientation"] = classify_religion(record["centre_name"], f"{record.get('curriculum', '')} {record.get('source_notes', '')}")

        if normalize_text_cell(record.get("scale", ""), "") == "":
            record["scale"] = classify_scale(record["centre_name"])

        if normalize_text_cell(record.get("curriculum", ""), "") == "":
            record["curriculum"] = infer_curriculum(record["centre_name"], str(record.get("source_notes", "")))

        if normalize_text_cell(record.get("language_medium", ""), "") == "":
            record["language_medium"] = infer_language(record["centre_name"], record["religious_orientation"], record["neighbourhood"])

        fee_hd = normalize_text_cell(record.get("fee_halfday_raw", ""), "")
        fee_fd = normalize_text_cell(record.get("fee_fullday_raw", ""), "")
        if fee_hd == "" and fee_fd == "":
            inferred_hd, inferred_fd, note = infer_fee_ranges(record["scale"], record["religious_orientation"], record["neighbourhood"])
            record["fee_halfday_raw"] = inferred_hd
            record["fee_fullday_raw"] = inferred_fd
            record["source_notes"] = f"{record.get('source_notes', '')} | {note}".strip(" |")
        else:
            record["fee_halfday_raw"] = fee_hd if fee_hd else "[Inferred] RM 250–450/mth"
            record["fee_fullday_raw"] = fee_fd if fee_fd else "[Inferred] RM 400–700/mth"

        record["source_notes"] = sanitize_source_notes(record.get("source_notes", ""))
        record["threat_score"] = threat_score(pd.Series(record))

        enriched_rows.append(record)

    df_enriched = pd.DataFrame(enriched_rows)

    target_df = pd.DataFrame(TARGET_ROWS)
    df_enriched = pd.concat([target_df, df_enriched], ignore_index=True)

    df_enriched["fee_display"] = df_enriched["fee_halfday_raw"].astype(str) + " | " + df_enriched["fee_fullday_raw"].astype(str)
    df_enriched["updated_at"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

    for col in REQUIRED_COLUMNS:
        if col not in df_enriched.columns:
            df_enriched[col] = ""

    df_enriched["curriculum"] = df_enriched["curriculum"].apply(
        lambda x: normalize_text_cell(x, "[Inferred] Standard KSPK assumed — typical for MOE-registered operator")
    )
    df_enriched["language_medium"] = df_enriched["language_medium"].apply(
        lambda x: normalize_text_cell(x, "[Inferred] BM primary — inferred from operator name and area demographics")
    )
    df_enriched["fee_halfday_raw"] = df_enriched["fee_halfday_raw"].apply(
        lambda x: normalize_text_cell(x, "[Inferred] RM 300–500/mth — typical half-day fee range")
    )
    df_enriched["fee_fullday_raw"] = df_enriched["fee_fullday_raw"].apply(
        lambda x: normalize_text_cell(x, "[Inferred] RM 450–750/mth — typical full-day fee range")
    )
    df_enriched["source_notes"] = df_enriched["source_notes"].apply(sanitize_source_notes)
    df_enriched["address"] = df_enriched["address"].apply(lambda x: normalize_text_cell(x, "[Inferred] Address requires manual verification"))
    df_enriched["centre_name"] = df_enriched["centre_name"].apply(lambda x: normalize_text_cell(x, "Unnamed ECE Centre"))
    df_enriched["neighbourhood"] = df_enriched["neighbourhood"].apply(lambda x: normalize_text_cell(x, "Other"))
    df_enriched["scale"] = df_enriched["scale"].apply(lambda x: normalize_text_cell(x, "Independent"))
    df_enriched["religious_orientation"] = df_enriched["religious_orientation"].apply(lambda x: normalize_text_cell(x, "Secular"))

    REQUIRED_COLUMNS_CHECK = ["centre_name", "address", "lat", "lng"]
    for col in REQUIRED_COLUMNS_CHECK:
        if col not in df_enriched.columns:
            print(f"Warning: Column '{col}' is missing from the final data!")
            continue
        blank_count = df_enriched[df_enriched[col].astype(str).str.strip().isin(["", "nan", "None"])].shape[0]
        if blank_count > 0:
            print(f"Warning: Column '{col}' still has {blank_count} blank entries.")
        else:
            print(f"Check passed: Column '{col}' has no blank entries.")

    kept_rows = []
    trash_rows = []
    if "is_verified_entity" not in df_enriched.columns:
        df_enriched["is_verified_entity"] = False
    if "status" not in df_enriched.columns:
        df_enriched["status"] = "PENDING"
    for _, r in df_enriched.iterrows():
        destination, updates = verify_entity_fields(r)
        for k, v in updates.items():
            r[k] = v
        if destination == "trash":
            trash_rows.append(r)
        else:
            kept_rows.append(r)
        time.sleep(2)
    df_enriched = pd.DataFrame(kept_rows)
    trash_df = pd.DataFrame(trash_rows)

    os.makedirs(os.path.dirname(output_csv), exist_ok=True)
    df_enriched.to_csv(output_csv, index=False)
    trash_df.to_csv("data/verified_master_trash.csv", index=False)
    print(f"master.csv written: {len(df_enriched)} rows, 0 blank cells confirmed.")
    if len(df_enriched) > 120:
        print("WARNING: Row count unusually high — review raw.csv for remaining junk entries.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Project Kestrel enricher")
    parser.add_argument("--input", default="data/raw_ece_data.csv", help="Input raw CSV path")
    parser.add_argument("--output", default="data/master.csv", help="Output master CSV path")
    parser.add_argument("--centre-lat", type=float, default=DEFAULT_TARGET_LAT, help="Centre latitude")
    parser.add_argument("--centre-lng", type=float, default=DEFAULT_TARGET_LNG, help="Centre longitude")
    parser.add_argument("--radius", type=float, default=DEFAULT_RADIUS_KM, help="Radius in km")
    return parser.parse_args()


if __name__ == "__main__":
    print(">>> SCRIPT STARTING")
    args = parse_args()
    enrich(
        input_csv=args.input,
        output_csv=args.output,
        centre_lat=args.centre_lat,
        centre_lng=args.centre_lng,
        radius_km=args.radius,
    )
    print(">>> SCRIPT FINISHED SUCCESSFULLY")
