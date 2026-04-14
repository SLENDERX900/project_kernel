import io
import os
import re
import subprocess
from datetime import date

import folium
import pandas as pd
import plotly.express as px
import streamlit as st
from folium.plugins import MarkerCluster
from geopy.geocoders import Nominatim
from streamlit_folium import st_folium

DEFAULT_ADDRESS = "Persiaran Tebar Layar, Bukit Jelutong, 40150 Shah Alam, Selangor"
DEFAULT_LAT = 3.1022
DEFAULT_LNG = 101.5333
MASTER_PATH = "data/master.csv"
RAW_PATH = "data/raw.csv"

DISPLAY_COLUMNS_MAP = {
    "centre_name": "Centre Name",
    "brand": "Brand",
    "address": "Address / Landmark",
    "neighbourhood": "Neighbourhood",
    "curriculum": "Curriculum & Approach",
    "religious_orientation": "Religious Orientation",
    "language_medium": "Language Medium",
    "scale": "Scale",
    "fee_halfday": "Half-day Fee",
    "fee_fullday": "Full-day Fee",
    "verification_note": "Verification Note",
}

TARGET_B_FEE = 1175
NEIGHBOURHOOD_ORDER = ["Bukit Jelutong", "Setia Alam", "Denai Alam", "Elmina", "Glenmarie", "Other"]

st.set_page_config(
    page_title="Project Kestrel — ECE Intelligence Dashboard",
    layout="wide",
    initial_sidebar_state="expanded",
)


def parse_fee_value(text: str) -> float:
    if not isinstance(text, str):
        return 0.0
    nums = re.findall(r"\d+(?:,\d+)?", text)
    if not nums:
        return 0.0
    return float(nums[0].replace(",", ""))


def geocode_address(address: str):
    geolocator = Nominatim(user_agent="project-kestrel-dashboard")
    try:
        loc = geolocator.geocode(address)
        if loc:
            return float(loc.latitude), float(loc.longitude), None
    except Exception as exc:
        return None, None, str(exc)
    return None, None, "Address not found"


def _coalesce_str(series: pd.Series) -> pd.Series:
    """Return the series with NaN / 'nan' / whitespace-only values replaced by ''."""
    return series.fillna("").astype(str).str.strip().replace("nan", "")


def _deduplicate_master(df: pd.DataFrame) -> pd.DataFrame:
    """Merge rows that share the same centre_name (case-insensitive, stripped).

    For every column, the first non-blank value across sibling rows wins.
    This collapses duplicate entries that differ only in which fields are blank.
    """
    if df.empty or "centre_name" not in df.columns:
        return df

    df = df.copy()
    df["_dedup_key"] = df["centre_name"].fillna("").astype(str).str.strip().str.lower()

    def _merge_group(group: pd.DataFrame) -> pd.Series:
        if len(group) == 1:
            return group.iloc[0]
        merged = {}
        for col in group.columns:
            non_blank = (
                group[col]
                .fillna("")
                .astype(str)
                .str.strip()
                .replace("nan", "")
            )
            non_blank = non_blank[non_blank != ""]
            merged[col] = non_blank.iloc[0] if not non_blank.empty else ""
        return pd.Series(merged)

    deduped = (
        df.groupby("_dedup_key", sort=False, group_keys=False)
        .apply(_merge_group)
        .reset_index(drop=True)
    )
    return deduped.drop(columns=["_dedup_key"], errors="ignore")


@st.cache_data
def load_data() -> pd.DataFrame:
    if not os.path.exists(MASTER_PATH):
        return pd.DataFrame()

    df = pd.read_csv(MASTER_PATH)

    # ── Schema normalisation ──────────────────────────────────────────────────
    # The new enricher.py dropped several columns present in the old schema.
    # Map equivalents and fill missing columns with safe defaults so the rest
    # of the app never needs to know which enricher version produced the file.

    # verification_note (new enricher) → source_notes (what the app expects)
    if "source_notes" not in df.columns:
        if "verification_note" in df.columns:
            df["source_notes"] = df["verification_note"]
        else:
            df["source_notes"] = ""

    # Columns absent from the new enricher — add empty defaults
    _defaults: dict = {
        "fee_halfday_raw":   "",
        "fee_fullday_raw":   "",
        "source_primary":    "",
        "is_moe_registered": False,
        "threat_score":      "",
        "lat":               "",
        "lng":               "",
    }
    for col, default in _defaults.items():
        if col not in df.columns:
            df[col] = default

    # ── fee_display ───────────────────────────────────────────────────────────
    if "fee_display" not in df.columns:
        half = _coalesce_str(df["fee_halfday_raw"])
        full = _coalesce_str(df["fee_fullday_raw"])
        df["fee_display"] = half.where(half != "", "").str.cat(
            full.where(full != "", ""),
            sep=" | ",
            na_rep="",
        ).str.strip(" |").str.strip()

    # ── Deduplication ─────────────────────────────────────────────────────────
    # Merge rows of the same ECE that differ only in which fields are blank.
    df = _deduplicate_master(df)
    
    # ── Calculate Threat Scores ───────────────────────────────────────────────
    # Calculate threat scores for all centers that don't have them
    if 'threat_score' in df.columns:
        df['threat_score'] = df.apply(
            lambda row: calculate_threat_score(row) if str(row.get('threat_score', '')).strip() == '' or str(row.get('threat_score', '')).strip() == 'Reference'
            else float(row.get('threat_score', 0)), axis=1
        )
        # Round threat scores to integers for display
        df['threat_score'] = df['threat_score'].round(0).astype(int)
    else:
        # New schema doesn't have threat_score - skip calculation
        pass
    
    # ── Extract Coordinates from Geocode Data ───────────────────────────────────
    # Extract lat/lng from geocode_data if lat/lng columns are missing or empty
    if 'geocode_data' in df.columns:
        def extract_coordinates(row):
            # Skip if lat/lng already exist and are valid
            if pd.notna(row.get('lat')) and pd.notna(row.get('lng')):
                try:
                    lat = float(row['lat'])
                    lng = float(row['lng'])
                    if lat != 0.0 and lng != 0.0:
                        return row['lat'], row['lng']
                except (ValueError, TypeError):
                    pass
            
            # Try to extract from geocode_data
            geocode_data = row.get('geocode_data', '')
            if not geocode_data or str(geocode_data) == 'nan':
                return None, None
                
            try:
                # Handle different geocode_data formats
                if isinstance(geocode_data, str):
                    import json
                    geocode_dict = json.loads(geocode_data)
                    coords = geocode_dict.get('coordinates')
                    if coords and len(coords) == 2:
                        return float(coords[0]), float(coords[1])
                elif hasattr(geocode_data, 'get'):
                    coords = geocode_data.get('coordinates')
                    if coords and len(coords) == 2:
                        return float(coords[0]), float(coords[1])
            except (ValueError, TypeError, json.JSONDecodeError, AttributeError):
                pass
                
            return None, None
        
        # Apply coordinate extraction
        coordinates = df.apply(extract_coordinates, axis=1, result_type='expand')
        df['lat'] = coordinates[0]
        df['lng'] = coordinates[1]

    return df


def save_data(df: pd.DataFrame) -> None:
    os.makedirs(os.path.dirname(MASTER_PATH), exist_ok=True)
    df.to_csv(MASTER_PATH, index=False)
    st.cache_data.clear()


def run_pipeline(address: str, radius_km: int):
    fetch_cmd = ["python", "fetcher.py", "--address", address, "--radius", str(radius_km), "--output", RAW_PATH]
    enrich_cmd = [
        "python", "enricher.py", "--input", RAW_PATH, "--output", MASTER_PATH,
        "--centre-lat", str(st.session_state["resolved_lat"]), "--centre-lng", str(st.session_state["resolved_lng"]),
        "--radius", str(radius_km),
    ]

    try:
        fetch_proc = subprocess.run(fetch_cmd, capture_output=True, text=True, check=False)
        enrich_proc = subprocess.run(enrich_cmd, capture_output=True, text=True, check=False)

        if fetch_proc.returncode != 0:
            st.error("Fetcher failed. See logs below.")
        if enrich_proc.returncode != 0:
            st.error("Enricher failed. See logs below.")

        with st.expander("Pipeline logs"):
            st.code("$ " + " ".join(fetch_cmd) + "\n" + fetch_proc.stdout + "\n" + fetch_proc.stderr)
            st.code("$ " + " ".join(enrich_cmd) + "\n" + enrich_proc.stdout + "\n" + enrich_proc.stderr)

        if fetch_proc.returncode == 0 and enrich_proc.returncode == 0:
            st.success("Market scan complete.")
            st.cache_data.clear()
            st.rerun()
    except Exception as exc:
        st.error(f"Pipeline execution error: {exc}")


def split_targets(df: pd.DataFrame):
    if df.empty:
        return df.copy(), df.copy()
    # Check for target reference in verification_note column
    if "verification_note" in df.columns:
        mask = df["verification_note"].astype(str).str.contains("Reference — Target", na=False)
    elif "source_notes" in df.columns:
        mask = df["source_notes"].astype(str).str.contains("Reference — Target", na=False)
    else:
        mask = pd.Series([False] * len(df), index=df.index)
    return df[mask].copy(), df[~mask].copy()


def apply_filters(df_comp: pd.DataFrame):
    neighbourhoods = sorted([x for x in df_comp["neighbourhood"].dropna().unique().tolist() if str(x).strip()])
    orientations = sorted([x for x in df_comp["religious_orientation"].dropna().unique().tolist() if str(x).strip()])
    scales = sorted([x for x in df_comp["scale"].dropna().unique().tolist() if str(x).strip()])

    selected_neighbourhoods = st.sidebar.multiselect("Neighbourhood", neighbourhoods, default=neighbourhoods)
    selected_orientations = st.sidebar.multiselect("Religious Orientation", orientations, default=orientations)
    selected_scales = st.sidebar.multiselect("Scale", scales, default=scales)

    # Handle both old and new fee column naming
    if "fee_fullday_raw" in df_comp.columns:
        fee_col = "fee_fullday_raw"
    elif "fee_fullday" in df_comp.columns:
        fee_col = "fee_fullday"
    else:
        fee_col = None

    if fee_col:
        df_comp["_fee_num"] = df_comp[fee_col].apply(parse_fee_value)
    else:
        df_comp["_fee_num"] = 0

    fee_range = st.sidebar.slider("Fee range (RM)", 0, 3000, (0, 3000), step=50)

    if selected_neighbourhoods:
        filtered = df_comp[df_comp["neighbourhood"].isin(selected_neighbourhoods)]
    else:
        filtered = df_comp.copy()

    if selected_orientations:
        filtered = filtered[filtered["religious_orientation"].isin(selected_orientations)]
    if selected_scales:
        filtered = filtered[filtered["scale"].isin(selected_scales)]

    filtered = filtered[(filtered["_fee_num"] >= fee_range[0]) & (filtered["_fee_num"] <= fee_range[1])]
    return filtered.drop(columns=["_fee_num"], errors="ignore")


def style_comp_table(df_show: pd.DataFrame):
    def row_style(row):
        if "Reference — Target" in str(row.get("Verification Note", "")):
            return ["background-color: #4A4A4A; color: #FFD700"] * len(row)
        return [""] * len(row)

    def source_style(val):
        val = str(val)
        if "[Verified" in val or "verified" in val.lower():
            return "color: #228B22"
        if "[Inferred" in val or "estimated" in val.lower():
            return "color: #B8860B"
        return ""

    styler = df_show.style.apply(row_style, axis=1)
    if "Verification Note" in df_show.columns:
        styler = styler.map(source_style, subset=["Verification Note"])
    elif "Source / Notes" in df_show.columns:
        styler = styler.map(source_style, subset=["Source / Notes"])
    return styler


def table_to_excel(df_export: pd.DataFrame) -> bytes:
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        df_export.to_excel(writer, index=False, sheet_name="Competitive Table")
        workbook = writer.book
        worksheet = writer.sheets["Competitive Table"]

        header_fmt = workbook.add_format({"bold": True, "font_color": "#FFFFFF", "bg_color": "#1a1a2e"})
        target_fmt = workbook.add_format({"bg_color": "#4A4A4A", "font_color": "#FFD700"})
        inferred_fmt = workbook.add_format({"font_color": "#B8860B"})
        verified_fmt = workbook.add_format({"font_color": "#228B22"})

        for col_idx, col_name in enumerate(df_export.columns):
            worksheet.write(0, col_idx, col_name, header_fmt)
            col_vals = [str(col_name)] + df_export[col_name].astype(str).tolist()
            max_width = max(15, min(50, max(len(v) for v in col_vals) + 2))
            worksheet.set_column(col_idx, col_idx, max_width)

        source_col = df_export.columns.get_loc("Verification Note") if "Verification Note" in df_export.columns else df_export.columns.get_loc("Source / Notes") if "Source / Notes" in df_export.columns else None
        for r in range(1, len(df_export) + 1):
            if "Verification Note" in df_export.columns:
                src = str(df_export.iloc[r - 1].get("Verification Note", ""))
            else:
                src = str(df_export.iloc[r - 1].get("Source / Notes", ""))
            if "Reference — Target" in src:
                worksheet.set_row(r, cell_format=target_fmt)
            if source_col is not None:
                if "[Inferred" in src or "estimated" in src.lower():
                    worksheet.write(r, source_col, src, inferred_fmt)
                elif "[Verified" in src or "verified" in src.lower():
                    worksheet.write(r, source_col, src, verified_fmt)

    return output.getvalue()


def build_word_copy_text(df_export: pd.DataFrame) -> str:
    return "\n".join(["\t".join(df_export.columns)] + ["\t".join(map(str, row)) for row in df_export.values])


def calculate_threat_score(row: pd.Series) -> float:
    """
    Calculate threat score based on 5-factor rubric (0-10 scale).
    """
    score = 0.0
    
    # 1. Curriculum overlap (0-3 pts)
    curriculum = str(row.get('curriculum', '')).lower()
    if any(term in curriculum for term in ['cambridge', 'eyfs', 'british']):
        score += 3
    elif any(term in curriculum for term in ['islamic', 'kspk', 'moe']):
        score += 2
    elif any(term in curriculum for term in ['montessori', 'play-based', 'reggio']):
        score += 1
    
    # 2. Religious orientation (0-2 pts)
    religious = str(row.get('religious_orientation', '')).lower()
    if any(term in religious for term in ['islamic', 'muslim']):
        score += 2
    elif any(term in religious for term in ['international', 'bilingual']):
        score += 1
    
    # 3. Scale/reach (0-2 pts)
    scale = str(row.get('scale', '')).lower()
    if any(term in scale for term in ['national', 'chain', 'franchise']):
        score += 2
    elif any(term in scale for term in ['regional', 'multiple']):
        score += 1
    
    # 4. Fee band overlap (0-2 pts)
    fee_text = str(row.get('fee_fullday_raw', '')).lower()
    fee = parse_fee_value(fee_text)
    if 940 <= fee <= 1410:  # Brand B band
        score += 2
    elif 300 <= fee <= 700:  # Brand A band
        score += 1
    
    # 5. Neighbourhood proximity (0-1 pt)
    neighbourhood = str(row.get('neighbourhood', '')).lower()
    if 'bukit jelutong' in neighbourhood:
        score += 1
    
    return min(score, 10.0)  # Cap at 10


def make_threat_tier(score) -> str:
    try:
        score = float(score)
    except Exception:
        return ""
    if score >= 8:
        return "🔴 High Threat"
    if score >= 5:
        return "🟡 Medium Threat"
    return "🟢 Low Threat"


def coverage_panel(df_comp: pd.DataFrame):
    counts = {area: int((df_comp["neighbourhood"] == area).sum()) for area in ["Bukit Jelutong", "Setia Alam", "Denai Alam", "Elmina"]}
    other = int(len(df_comp) - sum(counts.values()))

    st.sidebar.markdown(f"📊 **Coverage: {len(df_comp)} operators found**")
    st.sidebar.markdown(
        f"Bukit Jelutong: {counts['Bukit Jelutong']} | Setia Alam: {counts['Setia Alam']} | "
        f"Denai Alam: {counts['Denai Alam']} | Elmina: {counts['Elmina']} | Other: {other}"
    )
    for area, val in {**counts, "Other": other}.items():
        if val == 0:
            st.sidebar.warning(f"⚠️ {area} — No operators found. Consider manual search.")


def render_tab1(df_targets: pd.DataFrame, df_competitors: pd.DataFrame):
    st.subheader("Tab 1 — Competitive Landscape Table")

    st.markdown(f"**Showing {len(df_competitors)} competitors | {len(df_targets)} Target reference rows (always shown)**")

    # Align columns before concatenation to handle schema differences
    df_targets = df_targets.copy()
    df_competitors = df_competitors.copy()
    all_cols = set(df_targets.columns) | set(df_competitors.columns)
    for col in all_cols:
        if col not in df_targets.columns:
            df_targets[col] = ""
        if col not in df_competitors.columns:
            df_competitors[col] = ""
    display = pd.concat([df_targets, df_competitors], ignore_index=True)

    cols = [c for c in ["centre_name", "brand", "address", "neighbourhood", "curriculum", "religious_orientation", "language_medium", "scale", "fee_halfday", "fee_fullday", "verification_note"] if c in display.columns]
    display = display[cols].rename(columns=DISPLAY_COLUMNS_MAP)

    st.dataframe(style_comp_table(display), width="stretch", height=500)

    csv_data = display.to_csv(index=False).encode("utf-8")
    excel_data = table_to_excel(display)
    word_text = build_word_copy_text(display)

    c1, c2, c3 = st.columns(3)
    c1.download_button("Download as CSV", data=csv_data, file_name="competitive_landscape.csv", mime="text/csv")
    c2.download_button("Download as Excel (.xlsx)", data=excel_data, file_name="competitive_landscape.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    c3.download_button("Copy table for Word", data=word_text, file_name="competitive_landscape.txt", mime="text/plain")


def render_tab2(df_all: pd.DataFrame, radius_km: int, lat: float, lng: float):
    st.subheader("Tab 2 — Threat Map")

    # Check if data has coordinates
    has_lat = 'lat' in df_all.columns
    has_lng = 'lng' in df_all.columns
    has_geocode = 'geocode_data' in df_all.columns

    if not (has_lat or has_lng or has_geocode):
        st.warning("⚠️ Map unavailable: Current data schema does not include coordinates (lat/lng). Run enrichment pipeline to add geocoding data.")
        return

    m = folium.Map(location=[lat, lng], zoom_start=12)
    folium.Circle(
        location=[lat, lng],
        radius=radius_km * 1000,
        color="#1f77b4",
        fill=True,
        fill_color="#1f77b4",
        fill_opacity=0.2,
        popup=f"Search radius: {radius_km}km"
    ).add_to(m)

    # Check if threat_score exists for threat filtering
    has_threat = 'threat_score' in df_all.columns
    if has_threat:
        show_high_threat = st.checkbox("🔴 High Threat (8-10)", value=True)
        show_medium_threat = st.checkbox("🟡 Medium Threat (5-7)", value=True)
        show_low_threat = st.checkbox("🟢 Low Threat (1-4)", value=True)
    else:
        st.info("ℹ️ Threat scoring not available in current data schema")
        show_high_threat = True
        show_medium_threat = True
        show_low_threat = True

    show_targets = st.checkbox("Show Target reference pins", value=True)

    layer_targets = folium.FeatureGroup(name="Target")
    layer_high = folium.FeatureGroup(name="High Threat")
    layer_medium = folium.FeatureGroup(name="Medium Threat")
    layer_low = folium.FeatureGroup(name="Low Threat")

    cluster = MarkerCluster(name="Clusters")

    st.write(f"Debug: Data has lat: {has_lat}, lng: {has_lng}, geocode_data: {has_geocode}, threat_score: {has_threat}")
    st.write(f"Debug: Total rows: {len(df_all)}")

    markers_added = 0
    coordinates_extracted = 0

    for _, row in df_all.iterrows():
        lat_val, lng_val = None, None
        
        # Try to get coordinates from lat/lng columns first
        try:
            lat_val = float(row.get("lat", ""))
            lng_val = float(row.get("lng", ""))
            if lat_val and lng_val and not (lat_val == 0.0 and lng_val == 0.0):
                coordinates_extracted += 1
        except (ValueError, TypeError):
            pass
        
        # If lat/lng failed, try to extract from geocode_data
        if not lat_val or not lng_val:
            geocode_data = row.get('geocode_data', '')
            if geocode_data and str(geocode_data) != 'nan':
                try:
                    # Handle different geocode_data formats
                    if isinstance(geocode_data, str):
                        # Try to parse as JSON
                        import json
                        geocode_dict = json.loads(geocode_data)
                        coords = geocode_dict.get('coordinates')
                        if coords and len(coords) == 2:
                            lat_val, lng_val = float(coords[0]), float(coords[1])
                            coordinates_extracted += 1
                    elif hasattr(geocode_data, 'get'):
                        # If it's already a dict-like object
                        coords = geocode_data.get('coordinates')
                        if coords and len(coords) == 2:
                            lat_val, lng_val = float(coords[0]), float(coords[1])
                            coordinates_extracted += 1
                except (ValueError, TypeError, json.JSONDecodeError, AttributeError):
                    pass
        
        # Skip if still no valid coordinates or if values are NaN
        if not lat_val or not lng_val or pd.isna(lat_val) or pd.isna(lng_val):
            continue

        # Check for target reference in both old and new schema
        is_target = "Reference — Target" in str(row.get("verification_note", "")) or "Reference — Target" in str(row.get("source_notes", ""))

        # Get threat score and round to integer (if available)
        threat_score = 0
        if has_threat:
            try:
                threat_score = int(round(float(row.get("threat_score", 0))))
            except (ValueError, TypeError):
                threat_score = 0

        # Build popup with available data
        popup = (
            f"<b>{row.get('centre_name', '')}</b><br>"
            f"Address: {row.get('address', '')}<br>"
            f"Neighbourhood: {row.get('neighbourhood', '')}<br>"
            f"Scale: {row.get('scale', '')}<br>"
        )

        # Add fee information from either old or new schema
        if "fee_fullday_raw" in row:
            popup += f"Full-day fee: {row.get('fee_fullday_raw', '')}<br>"
        elif "fee_fullday" in row:
            popup += f"Full-day fee: {row.get('fee_fullday', '')}<br>"

        popup += f"Curriculum: {row.get('curriculum', '')}<br>"

        if has_threat:
            popup += f"<b>Threat Score: {threat_score}/10</b>"

        if is_target and show_targets:
            folium.Marker(
                [lat_val, lng_val],
                popup=popup,
                icon=folium.Icon(color="orange", icon="star"),
            ).add_to(layer_targets)
            markers_added += 1
            continue

        # Color based on threat score (if available)
        if has_threat:
            if threat_score >= 8 and show_high_threat:
                folium.Marker(
                    [lat_val, lng_val],
                    popup=popup,
                    icon=folium.Icon(color="red", icon="exclamation-triangle")
                ).add_to(layer_high)
                markers_added += 1
            elif 5 <= threat_score <= 7 and show_medium_threat:
                folium.Marker(
                    [lat_val, lng_val],
                    popup=popup,
                    icon=folium.Icon(color="orange", icon="warning")
                ).add_to(layer_medium)
                markers_added += 1
            elif 1 <= threat_score <= 4 and show_low_threat:
                folium.Marker(
                    [lat_val, lng_val],
                    popup=popup,
                    icon=folium.Icon(color="green", icon="info-sign")
                ).add_to(layer_low)
                markers_added += 1
        else:
            # No threat scoring - show all markers in blue
            folium.Marker(
                [lat_val, lng_val],
                popup=popup,
                icon=folium.Icon(color="blue", icon="info-sign")
            ).add_to(layer_low)
            markers_added += 1

        # Add to cluster
        folium.Marker([lat_val, lng_val], popup=popup).add_to(cluster)
        markers_added += 1
    
    st.write(f"Debug: Coordinates extracted: {coordinates_extracted}, Markers added: {markers_added}")

    # Add layers to map
    if show_targets:
        layer_targets.add_to(m)
    if show_high_threat:
        layer_high.add_to(m)
    if show_medium_threat:
        layer_medium.add_to(m)
    if show_low_threat:
        layer_low.add_to(m)

    cluster.add_to(m)

    legend_html = """
    <div style='position: fixed; bottom: 40px; left: 40px; z-index: 9999; background-color: white; padding: 10px; border: 1px solid #ccc; color: black;'>
      <b>Threat Map Legend</b><br>
      <span style='color:orange;'>★</span> Target Reference<br>
      <span style='color:red;'>▲</span> High Threat (8-10)<br>
      <span style='color:orange;'>▲</span> Medium Threat (5-7)<br>
      <span style='color:green;'>▲</span> Low Threat (1-4)<br>
      <small>Icons show threat level based on 5-factor rubric</small>
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend_html))

    folium.LayerControl().add_to(m)
    st_folium(m, width=None, height=600)

    st.info("💡 Take a screenshot of this map for your submission document.")

    map_html = m.get_root().render().encode("utf-8")
    st.download_button("Download map as HTML", data=map_html, file_name="threat_map.html", mime="text/html")


def render_tab3(df_comp: pd.DataFrame):
    st.subheader("Tab 3 — Memo Stats & Draft Builder")

    if df_comp.empty:
        st.warning("No competitor rows available.")
        return

    total = len(df_comp)
    
    # Fix religious orientation column names
    religious_col = 'religious_orientation'
    if religious_col not in df_comp.columns:
        religious_col = 'religious_orientation'  # fallback
    
    islamic_pct = 100 * (df_comp[religious_col].astype(str).str.contains('islamic', case=False).sum() / total)
    secular_pct = 100 * (df_comp[religious_col].astype(str).str.contains('secular|international', case=False).sum() / total)

    # Handle both old and new fee column naming
    fee_col = "fee_fullday_raw" if "fee_fullday_raw" in df_comp.columns else "fee_fullday" if "fee_fullday" in df_comp.columns else None

    # Initialize premium_gap and area averages with default values
    premium_gap = TARGET_B_FEE
    avg_all = 0
    avg_bj = 0
    avg_sa = 0
    avg_el = 0

    if fee_col:
        fee_num = df_comp[fee_col].astype(str).map(parse_fee_value)
        avg_all = fee_num[fee_num > 0].mean() if (fee_num > 0).any() else 0

        def avg_area(area):
            vals = df_comp[df_comp["neighbourhood"].astype(str).str.contains(area, case=False, na=False)][fee_col].astype(str).map(parse_fee_value)
            vals = vals[vals > 0]
            return vals.mean() if not vals.empty else 0

        avg_bj = avg_area("Bukit Jelutong")
        avg_sa = avg_area("Setia Alam")
        avg_el = avg_area("Elmina")

        premium_gap = TARGET_B_FEE - fee_num[fee_num > 0].max() if (fee_num > 0).any() else TARGET_B_FEE
    else:
        avg_all = 0
        avg_bj = 0
        avg_sa = 0
        avg_el = 0
        premium_gap = TARGET_B_FEE

    dens = df_comp["neighbourhood"].value_counts()
    lowest_density = dens.idxmin() if not dens.empty else "N/A"
    highest_density = dens.idxmax() if not dens.empty else "N/A"
    elmina_count = dens.get("Elmina", 0)

    # Fix scale column names
    scale_col = 'scale'
    if scale_col not in df_comp.columns:
        scale_col = 'scale'
    
    national_chains = int((df_comp["scale"] == "National Chain").sum())
    independent_ops = int((df_comp["scale"] == "Independent").sum())

    # Fix source column names
    source_col = "verification_note" if "verification_note" in df_comp.columns else "source_notes" if "source_notes" in df_comp.columns else None
    if source_col:
        verified_rows = df_comp[source_col].astype(str).str.contains(r"\[Verified|Verified", case=False, regex=True).sum()
        inferred_rows = df_comp[source_col].astype(str).str.contains(r"\[Inferred|Inferred|estimated", case=False, regex=True).sum()
    else:
        verified_rows = 0
        inferred_rows = total

    # Get threat score statistics (if available)
    has_threat = 'threat_score' in df_comp.columns
    if has_threat:
        high_threat = df_comp['threat_score'].astype(float).ge(8).sum()
        medium_threat = df_comp['threat_score'].astype(float).between(5, 7).sum()
        low_threat = df_comp['threat_score'].astype(float).lt(5).sum()
        top_threats = df_comp.nlargest(3, 'threat_score')[['centre_name', 'address', 'threat_score']].to_string(index=False)
    else:
        high_threat = 0
        medium_threat = 0
        low_threat = total
        top_threats = "Threat scoring not available in current data schema"

    source_labels = {
        "Overpass API (OpenStreetMap)",
        "data.gov.my Open API", 
        "Kiddy123 Directory",
        "Foursquare Places API",
        "SerpAPI (Google Search)",
        "Tavily",
        "ArcGIS",
        "TomTom"
    }
    
    # Extract sources from source_notes
    all_sources = []
    if source_col:
        for notes in df_comp[source_col].dropna().astype(str):
            if "Tavily" in notes:
                all_sources.append("Tavily")
            if "ArcGIS" in notes:
                all_sources.append("ArcGIS")
            if "TomTom" in notes:
                all_sources.append("TomTom")
    found_sources = sorted(set(all_sources))

    c1, c2, c3 = st.columns(3)
    c1.metric("Total competitors", total)
    c2.metric("% Islamic", f"{islamic_pct:.1f}%")
    c3.metric("% Secular", f"{secular_pct:.1f}%")

    c1, c2, c3 = st.columns(3)
    c1.metric("Avg full-day fee (all)", f"RM {avg_all:,.0f}")
    c2.metric("Avg fee — Bukit Jelutong", f"RM {avg_bj:,.0f}")
    c3.metric("Avg fee — Setia Alam", f"RM {avg_sa:,.0f}")

    c1, c2, c3 = st.columns(3)
    if has_threat:
        c1.metric("Premium fee gap (Target B vs nearest rival)", f"RM {premium_gap:,.0f}")
    else:
        c1.metric("Premium fee gap", "N/A")
    c2.metric("Lowest-density area", lowest_density)
    c3.metric("Verified vs Inferred", f"{verified_rows} vs {inferred_rows}")

    c1, c2, c3 = st.columns(3)
    c1.metric("National chains", int(national_chains))
    c2.metric("Independent operators", int(independent_ops))
    c3.metric("Total operators", total)

    c1, c2, c3 = st.columns(3)
    c1.metric("🔴 High threats", int(high_threat))
    c2.metric("🟡 Medium threats", int(medium_threat))
    c3.metric("🟢 Low threats", int(low_threat))

    if elmina_count < 3:
        st.warning("⚠️ Elmina coverage may be incomplete. Recommend supplementing with manual Google Maps / Facebook search for 'taska Kota Elmina' and 'tadika Elmina Shah Alam'.")

    st.info(f"Data drawn from {len(found_sources)} sources: {', '.join(found_sources) if found_sources else 'None'}")

    chart_df = (
        df_comp.assign(_fee=df_comp["fee_fullday_raw"].astype(str).map(parse_fee_value))
        .query("_fee > 0")
        .groupby("neighbourhood", as_index=False)["_fee"]
        .mean()
    )
    if not chart_df.empty:
        fig = px.bar(chart_df, x="neighbourhood", y="_fee", title="Average Full-Day Fee by Neighbourhood")
        fig.add_hline(y=TARGET_B_FEE, line_dash="dash", annotation_text="Target Brand B")
        st.plotly_chart(fig, width="stretch")

    # Auto-fill insights based on actual data
    auto_insight_a = f"Found {total} ECE operators across Shah Alam corridor. "
    if highest_density != "N/A":
        auto_insight_a += f"Highest concentration in {highest_density} ({dens[highest_density]} operators). "
    if lowest_density != "N/A":
        auto_insight_a += f"Lowest concentration in {lowest_density} ({dens[lowest_density]} operators). "

    if has_threat:
        auto_insight_b = f"Top threats identified: {high_threat} high-threat operators. "
        if high_threat > 0:
            auto_insight_b += f"Key competitors:\n{top_threats}\n"
        if premium_gap > 0:
            auto_insight_b += f"Target Brand B commands RM{premium_gap:,.0f} premium above nearest competitor. "
        else:
            auto_insight_b += "Target Brand B pricing is competitive with market leaders. "
    else:
        auto_insight_b = "Threat scoring not available in current data schema. "

    auto_insight_c = f"Religious orientation split: {islamic_pct:.1f}% Islamic vs {secular_pct:.1f}% secular. "
    if elmina_count < 3:
        auto_insight_c += f"Elmina market appears underserved with only {elmina_count} operators. "
    if national_chains > independent_ops:
        auto_insight_c += f"National chains dominate ({national_chains} vs {independent_ops} independent). "
    else:
        auto_insight_c += f"Independent operators outnumber chains ({independent_ops} vs {national_chains}). "

    auto_insight_d = f"Market concentration analysis shows the Shah Alam corridor is "
    if has_threat:
        if high_threat > total * 0.3:
            auto_insight_d += "highly competitive with strong established players. "
        elif medium_threat > total * 0.5:
            auto_insight_d += "moderately competitive with room for differentiation. "
        else:
            auto_insight_d += "fragmented with opportunities for premium positioning. "
    else:
        auto_insight_d += "fragmented with opportunities for premium positioning. "
    auto_insight_d += f"Brand B should target {highest_density} area for maximum impact."

    st.warning(
        "⚠️ This is a draft framework only. The statistics above are auto-filled. "
        "You must write the actual analysis. Do not summarise the table — interpret what it means. "
        "Be direct. Have an opinion. Max 1 page (~400 words)."
    )

    author_name = st.text_input("From:", value="[Your Name]")

    sec_a = st.text_area("a) What did you find?", value=auto_insight_a, height=100)
    sec_b = st.text_area("b) Biggest threats to the Target", value=auto_insight_b, height=100)
    sec_c = st.text_area("c) Anything surprising?", value=auto_insight_c, height=100)
    sec_d = st.text_area("d) Overall read", value=auto_insight_d, height=100)

    memo = f"""MEMORANDUM
To:    The Team, Newmoon Capital
From:  {author_name}
Re:    Project Kestrel — Competitive Landscape, Shah Alam ECE Corridor
Date:  {date.today().isoformat()}

EXECUTIVE SUMMARY
• Total ECE operators analyzed: {total}"""

    if has_threat:
        memo += f"""
• High-threat competitors: {high_threat} | Medium-threat: {medium_threat} | Low-threat: {low_threat}
• Average market fees: RM{avg_all:,.0f}/month vs Target Brand B: RM{TARGET_B_FEE:,.0f}/month"""
    else:
        memo += f"""
• Average market fees: RM{avg_all:,.0f}/month"""

    memo += f"""
• Market concentration: {highest_density} ({dens[highest_density]} operators) to {lowest_density} ({dens[lowest_density]} operators)

a) What did you find?
{sec_a}

b) Biggest threats to the Target
{sec_b}

c) Anything surprising?
{sec_c}

d) Overall read
{sec_d}

---
Generated by Project Kestrel ECE Intelligence System
Data sources: {', '.join(found_sources) if found_sources else 'None'}
Last updated: {date.today().isoformat()}
"""

    st.code(memo, language="text")
    st.download_button("📄 Download memo as .txt", data=memo.encode("utf-8"), file_name=f"kestrel_memo_{date.today().strftime('%Y%m%d')}.txt", mime="text/plain")


def render_tab4(df_all: pd.DataFrame):
    st.subheader("Tab 4 — Manual Data Entry & Editing")

    required = [
        "centre_name", "address", "neighbourhood", "curriculum", "language_medium",
        "fee_halfday_raw", "fee_fullday_raw", "scale", "religious_orientation", "source_notes",
    ]

    st.markdown("### Section A — Add New Operator")
    with st.form("add_form"):
        new_data = {c: st.text_input(c) for c in required}
        submitted = st.form_submit_button("Add operator")
        if submitted:
            if any(not str(v).strip() for v in new_data.values()):
                st.error("All fields are required.")
            else:
                row = {**new_data, "source_primary": "Manual Entry", "is_moe_registered": False}
                # Add lat/lng if they exist in the schema
                if "lat" in df_all.columns:
                    row["lat"] = ""
                if "lng" in df_all.columns:
                    row["lng"] = ""
                # Calculate threat score if it exists in the schema
                df_new = pd.concat([df_all, pd.DataFrame([row])], ignore_index=True)
                if "threat_score" in df_new.columns:
                    df_new['threat_score'] = df_new.apply(calculate_threat_score, axis=1)
                save_data(df_new)
                st.success("Operator added.")
                st.rerun()

    st.markdown("### Section B — Edit Existing Row")
    if not df_all.empty:
        opts = df_all.index.tolist()
        edit_idx = st.selectbox("Select row index to edit", options=opts)
        record = df_all.loc[edit_idx].to_dict()
        with st.form("edit_form"):
            edited = {c: st.text_input(c, value=str(record.get(c, ""))) for c in required}
            save_edit = st.form_submit_button("Save Changes")
            if save_edit:
                for k, v in edited.items():
                    df_all.at[edit_idx, k] = v
                save_data(df_all)
                st.success("Row updated.")
                st.rerun()

    st.markdown("### Section C — Delete Row")
    if not df_all.empty:
        del_idx = st.selectbox("Select row index to delete", options=df_all.index.tolist(), key="delete_idx")
        # Check for target reference in both old and new schema
        if "verification_note" in df_all.columns:
            del_note = str(df_all.loc[del_idx, "verification_note"])
        elif "source_notes" in df_all.columns:
            del_note = str(df_all.loc[del_idx, "source_notes"])
        else:
            del_note = ""
        confirm = st.checkbox("Confirm deletion")
        if st.button("Delete selected row"):
            if "Reference — Target" in del_note:
                st.error("Target reference rows are protected and cannot be deleted.")
            elif not confirm:
                st.error("Please confirm deletion.")
            else:
                df_next = df_all.drop(index=del_idx).reset_index(drop=True)
                save_data(df_next)
                st.success("Row deleted.")
                st.rerun()

    st.markdown("### Section D — Bulk CSV Import")
    template = pd.DataFrame(columns=required)
    st.download_button("Download blank CSV template", data=template.to_csv(index=False).encode("utf-8"), file_name="manual_import_template.csv", mime="text/csv")

    upload = st.file_uploader("Upload CSV", type=["csv"])
    if upload is not None:
        up_df = pd.read_csv(upload)
        missing = [c for c in required if c not in up_df.columns]
        if missing:
            st.error(f"Missing required headers: {', '.join(missing)}")
        else:
            up_df = up_df.drop_duplicates(subset=["centre_name", "address"], keep="first")
            st.dataframe(up_df.head(30), width="stretch")
            if st.button("Confirm import"):
                up_df["source_primary"] = "Manual Entry"
                up_df["is_moe_registered"] = False
                # Add lat/lng if they exist in the schema
                if "lat" in df_all.columns:
                    up_df["lat"] = ""
                if "lng" in df_all.columns:
                    up_df["lng"] = ""
                # Calculate threat scores if it exists in the schema
                combined = pd.concat([df_all, up_df], ignore_index=True)
                if "threat_score" in combined.columns:
                    combined['threat_score'] = combined.apply(calculate_threat_score, axis=1)
                combined = combined.drop_duplicates(subset=["centre_name", "address"], keep="first")
                save_data(combined)
                st.success("Bulk import complete.")
                st.rerun()


def main():
    st.title("Project Kestrel — ECE Market Intelligence Dashboard")

    # Refresh data button
    col1, col2 = st.columns([1, 4])
    with col1:
        if st.button("🔄 Refresh Data", help="Reload data from master.csv"):
            st.cache_data.clear()
            st.rerun()
    
    with col2:
        st.markdown("**Real-time ECE Competitive Intelligence**")

    if "last_valid_lat" not in st.session_state:
        st.session_state["last_valid_lat"] = DEFAULT_LAT
        st.session_state["last_valid_lng"] = DEFAULT_LNG
        st.session_state["resolved_lat"] = DEFAULT_LAT
        st.session_state["resolved_lng"] = DEFAULT_LNG

    st.sidebar.header("Controls")
    address = st.sidebar.text_input("Address", value=DEFAULT_ADDRESS)
    radius_km = st.sidebar.slider("Search radius: X km", min_value=1, max_value=15, value=10)

    lat, lng, err = geocode_address(address)
    if lat is not None and lng is not None:
        st.session_state["resolved_lat"] = lat
        st.session_state["resolved_lng"] = lng
        st.session_state["last_valid_lat"] = lat
        st.session_state["last_valid_lng"] = lng
        st.sidebar.success(f"📍 Resolved: {lat:.4f}° N, {lng:.4f}° E")
    else:
        st.session_state["resolved_lat"] = st.session_state["last_valid_lat"]
        st.session_state["resolved_lng"] = st.session_state["last_valid_lng"]
        st.sidebar.warning("⚠️ Address not found. Using last valid coordinates.")
        if err:
            st.sidebar.caption(err)

    if st.sidebar.button("Run Market Scan"):
        run_pipeline(address, radius_km)

    if st.sidebar.button("Reload Data"):
        st.cache_data.clear()
        st.rerun()

    df = load_data()
    if df.empty:
        st.warning("No data found at data/master.csv. Run Market Scan first.")
        return

    df_targets, df_competitors = split_targets(df)
    df_competitors = apply_filters(df_competitors)

    coverage_panel(df_competitors)

    if int((df_competitors["neighbourhood"] == "Elmina").sum()) < 3:
        st.warning("⚠️ Elmina coverage may be incomplete. Recommend supplementing with manual Google Maps / Facebook search for 'taska Kota Elmina' and 'tadika Elmina Shah Alam'.")

    t1, t2, t3, t4 = st.tabs(["Competitive Table", "Threat Map", "Memo Builder", "Manual Edit"])

    with t1:
        render_tab1(df_targets, df_competitors)

    with t2:
        # Align columns before concatenation to handle schema differences
        df_targets_copy = df_targets.copy()
        df_competitors_copy = df_competitors.copy()
        all_cols = set(df_targets_copy.columns) | set(df_competitors_copy.columns)
        for col in all_cols:
            if col not in df_targets_copy.columns:
                df_targets_copy[col] = ""
            if col not in df_competitors_copy.columns:
                df_competitors_copy[col] = ""
        map_df = pd.concat([df_targets_copy, df_competitors_copy], ignore_index=True)
        render_tab2(map_df, radius_km, st.session_state["resolved_lat"], st.session_state["resolved_lng"])

    with t3:
        render_tab3(df_competitors)

    with t4:
        render_tab4(df)


if __name__ == "__main__":
    main()