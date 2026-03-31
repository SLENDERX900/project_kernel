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
    "centre_name": "Centre Name & Brand",
    "address": "Address / Landmark",
    "neighbourhood": "Neighbourhood",
    "curriculum": "Curriculum & Approach",
    "language_medium": "Language Medium",
    "fee_display": "Monthly Fees",
    "scale": "Scale",
    "religious_orientation": "Religious Orientation",
    "source_notes": "Source / Notes",
    "threat_score": "Threat Score",
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


@st.cache_data
def load_data() -> pd.DataFrame:
    if not os.path.exists(MASTER_PATH):
        return pd.DataFrame()
    df = pd.read_csv(MASTER_PATH)
    if "fee_display" not in df.columns:
        df["fee_display"] = df["fee_halfday_raw"].astype(str) + " | " + df["fee_fullday_raw"].astype(str)
    if "threat_score" not in df.columns:
        df["threat_score"] = ""
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
    mask = df["source_notes"].astype(str).str.contains("Reference — Target", na=False)
    return df[mask].copy(), df[~mask].copy()


def apply_filters(df_comp: pd.DataFrame):
    neighbourhoods = sorted([x for x in df_comp["neighbourhood"].dropna().unique().tolist() if str(x).strip()])
    orientations = sorted([x for x in df_comp["religious_orientation"].dropna().unique().tolist() if str(x).strip()])
    scales = sorted([x for x in df_comp["scale"].dropna().unique().tolist() if str(x).strip()])

    selected_neighbourhoods = st.sidebar.multiselect("Neighbourhood", neighbourhoods, default=neighbourhoods)
    selected_orientations = st.sidebar.multiselect("Religious Orientation", orientations, default=orientations)
    selected_scales = st.sidebar.multiselect("Scale", scales, default=scales)

    fee_vals = df_comp["fee_fullday_raw"].astype(str).map(parse_fee_value)
    filtered = df_comp.copy()
    filtered["_fee_num"] = fee_vals

    fee_range = st.sidebar.slider("Fee range (RM)", 0, 3000, (0, 3000), step=50)

    if selected_neighbourhoods:
        filtered = filtered[filtered["neighbourhood"].isin(selected_neighbourhoods)]
    if selected_orientations:
        filtered = filtered[filtered["religious_orientation"].isin(selected_orientations)]
    if selected_scales:
        filtered = filtered[filtered["scale"].isin(selected_scales)]

    filtered = filtered[(filtered["_fee_num"] >= fee_range[0]) & (filtered["_fee_num"] <= fee_range[1])]
    return filtered.drop(columns=["_fee_num"], errors="ignore")


def style_comp_table(df_show: pd.DataFrame):
    def row_style(row):
        if "Reference — Target" in str(row.get("Source / Notes", "")):
            return ["background-color: #FFF3CD"] * len(row)
        return [""] * len(row)

    def source_style(val):
        val = str(val)
        if "[Verified" in val:
            return "color: #228B22"
        if "[Inferred" in val:
            return "color: #B8860B"
        return ""

    styler = df_show.style.apply(row_style, axis=1)
    if "Source / Notes" in df_show.columns:
        styler = styler.map(source_style, subset=["Source / Notes"])
    return styler


def table_to_excel(df_export: pd.DataFrame) -> bytes:
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        df_export.to_excel(writer, index=False, sheet_name="Competitive Table")
        workbook = writer.book
        worksheet = writer.sheets["Competitive Table"]

        header_fmt = workbook.add_format({"bold": True, "font_color": "#FFFFFF", "bg_color": "#1a1a2e"})
        target_fmt = workbook.add_format({"bg_color": "#FFF3CD"})
        inferred_fmt = workbook.add_format({"font_color": "#B8860B"})
        verified_fmt = workbook.add_format({"font_color": "#228B22"})

        for col_idx, col_name in enumerate(df_export.columns):
            worksheet.write(0, col_idx, col_name, header_fmt)
            col_vals = [str(col_name)] + df_export[col_name].astype(str).tolist()
            max_width = max(15, min(50, max(len(v) for v in col_vals) + 2))
            worksheet.set_column(col_idx, col_idx, max_width)

        source_col = df_export.columns.get_loc("Source / Notes") if "Source / Notes" in df_export.columns else None
        for r in range(1, len(df_export) + 1):
            src = str(df_export.iloc[r - 1].get("Source / Notes", ""))
            if "Reference — Target" in src:
                worksheet.set_row(r, cell_format=target_fmt)
            if source_col is not None:
                if "[Inferred" in src:
                    worksheet.write(r, source_col, src, inferred_fmt)
                elif "[Verified" in src:
                    worksheet.write(r, source_col, src, verified_fmt)

    return output.getvalue()


def build_word_copy_text(df_export: pd.DataFrame) -> str:
    return "\n".join(["\t".join(df_export.columns)] + ["\t".join(map(str, row)) for row in df_export.values])


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

    show_badges = st.toggle("Show threat tier badge", value=False)
    show_threat_score = st.toggle("Show Threat Score column", value=True)

    comp = df_competitors.copy()
    comp["Threat Tier"] = comp["threat_score"].apply(make_threat_tier)

    st.markdown(f"**Showing {len(comp)} competitors | {len(df_targets)} Target reference rows (always shown)**")

    display = pd.concat([df_targets, comp], ignore_index=True)
    if not show_threat_score and "threat_score" in display.columns:
        display = display.drop(columns=["threat_score"])
    if not show_badges and "Threat Tier" in display.columns:
        display = display.drop(columns=["Threat Tier"])

    cols = [c for c in ["centre_name", "address", "neighbourhood", "curriculum", "language_medium", "fee_display", "scale", "religious_orientation", "source_notes", "threat_score", "Threat Tier"] if c in display.columns]
    display = display[cols].rename(columns=DISPLAY_COLUMNS_MAP)

    st.dataframe(style_comp_table(display), use_container_width=True, height=500)

    csv_data = display.to_csv(index=False).encode("utf-8")
    excel_data = table_to_excel(display)
    word_text = build_word_copy_text(display)

    c1, c2, c3 = st.columns(3)
    c1.download_button("Download as CSV", data=csv_data, file_name="competitive_landscape.csv", mime="text/csv")
    c2.download_button("Download as Excel (.xlsx)", data=excel_data, file_name="competitive_landscape.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    c3.download_button("Copy table for Word", data=word_text, file_name="competitive_landscape.txt", mime="text/plain")


def render_tab2(df_all: pd.DataFrame, radius_km: int, lat: float, lng: float):
    st.subheader("Tab 2 — Threat Map")

    m = folium.Map(location=[lat, lng], zoom_start=12)
    folium.Circle(
        location=[lat, lng],
        radius=radius_km * 1000,
        color="#1f77b4",
        fill=True,
        fill_opacity=0.08,
        dash_array="8,6",
    ).add_to(m)

    show_islamic = st.checkbox("Show Islamic operators", value=True)
    show_premium = st.checkbox("Show Premium operators", value=True)
    show_secular = st.checkbox("Show Secular / other operators", value=True)
    show_targets = st.checkbox("Show Target reference pins", value=True)

    layer_targets = folium.FeatureGroup(name="Target")
    layer_islamic = folium.FeatureGroup(name="Islamic")
    layer_premium = folium.FeatureGroup(name="Premium")
    layer_secular = folium.FeatureGroup(name="Secular/Other")

    cluster = MarkerCluster(name="Clusters")

    for _, row in df_all.iterrows():
        if pd.isna(row.get("lat")) or pd.isna(row.get("lng")):
            continue

        is_target = "Reference — Target" in str(row.get("source_notes", ""))
        popup = (
            f"<b>{row.get('centre_name', '')}</b><br>"
            f"Neighbourhood: {row.get('neighbourhood', '')}<br>"
            f"Scale: {row.get('scale', '')}<br>"
            f"Full-day fee: {row.get('fee_fullday_raw', '')}<br>"
            f"Curriculum: {row.get('curriculum', '')}"
        )

        if is_target and show_targets:
            folium.Marker(
                [row["lat"], row["lng"]],
                popup=popup,
                icon=folium.Icon(color="orange", icon="star"),
            ).add_to(layer_targets)
            continue

        orientation = str(row.get("religious_orientation", ""))
        scale = str(row.get("scale", ""))

        if orientation == "Islamic-integrated" and show_islamic:
            folium.Marker([row["lat"], row["lng"]], popup=popup, icon=folium.Icon(color="green")).add_to(layer_islamic)
        elif scale == "Institutional Campus" and show_premium:
            folium.Marker([row["lat"], row["lng"]], popup=popup, icon=folium.Icon(color="red")).add_to(layer_premium)
        elif show_secular:
            folium.Marker([row["lat"], row["lng"]], popup=popup, icon=folium.Icon(color="blue")).add_to(layer_secular)

        folium.Marker([row["lat"], row["lng"]], popup=popup).add_to(cluster)

    if show_targets:
        layer_targets.add_to(m)
    if show_islamic:
        layer_islamic.add_to(m)
    if show_premium:
        layer_premium.add_to(m)
    if show_secular:
        layer_secular.add_to(m)

    cluster.add_to(m)

    legend_html = """
    <div style='position: fixed; bottom: 40px; left: 40px; z-index: 9999; background-color: white; padding: 10px; border: 1px solid #ccc;'>
      <b>Legend</b><br>
      <span style='color:orange;'>●</span> Target<br>
      <span style='color:green;'>●</span> Islamic-integrated<br>
      <span style='color:red;'>●</span> Premium / Institutional<br>
      <span style='color:blue;'>●</span> Secular / Other
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
    islamic_pct = 100 * (df_comp["religious_orientation"].astype(str).eq("Islamic-integrated").sum() / total)
    secular_pct = 100 * (df_comp["religious_orientation"].astype(str).eq("Secular").sum() / total)

    fee_num = df_comp["fee_fullday_raw"].astype(str).map(parse_fee_value)
    avg_all = fee_num[fee_num > 0].mean() if (fee_num > 0).any() else 0

    def avg_area(area):
        vals = df_comp[df_comp["neighbourhood"] == area]["fee_fullday_raw"].astype(str).map(parse_fee_value)
        vals = vals[vals > 0]
        return vals.mean() if not vals.empty else 0

    avg_bj = avg_area("Bukit Jelutong")
    avg_sa = avg_area("Setia Alam")
    avg_el = avg_area("Elmina")

    premium_gap = TARGET_B_FEE - fee_num[fee_num > 0].max() if (fee_num > 0).any() else TARGET_B_FEE

    dens = df_comp["neighbourhood"].value_counts()
    lowest_density = dens.idxmin() if not dens.empty else "N/A"
    highest_density = dens.idxmax() if not dens.empty else "N/A"

    national_chains = df_comp["scale"].astype(str).eq("National Chain").sum()
    independent_ops = df_comp["scale"].astype(str).eq("Independent").sum()
    elmina_count = int((df_comp["neighbourhood"] == "Elmina").sum())

    verified_rows = df_comp["source_notes"].astype(str).str.contains("\[Verified", regex=True).sum()
    inferred_rows = df_comp["source_notes"].astype(str).str.contains("\[Inferred", regex=True).sum()

    source_labels = {
        "Overpass API (OpenStreetMap)",
        "data.gov.my Open API",
        "Kiddy123 Directory",
        "Foursquare Places API",
        "SerpAPI (Google Search)",
    }
    found_sources = sorted(source_labels.intersection(set(df_comp["source_primary"].dropna().astype(str).tolist())))

    c1, c2, c3 = st.columns(3)
    c1.metric("Total competitors", total)
    c2.metric("% Islamic", f"{islamic_pct:.1f}%")
    c3.metric("% Secular", f"{secular_pct:.1f}%")

    c1, c2, c3 = st.columns(3)
    c1.metric("Avg full-day fee (all)", f"RM {avg_all:,.0f}")
    c2.metric("Avg fee — Bukit Jelutong", f"RM {avg_bj:,.0f}")
    c3.metric("Avg fee — Setia Alam", f"RM {avg_sa:,.0f}")

    c1, c2, c3 = st.columns(3)
    c1.metric("Premium fee gap (Target B vs nearest rival)", f"RM {premium_gap:,.0f}")
    c2.metric("Lowest-density area", lowest_density)
    c3.metric("Verified vs Inferred split", f"{verified_rows} vs {inferred_rows}")

    c1, c2, c3 = st.columns(3)
    c1.metric("National chains", int(national_chains))
    c2.metric("Independent operators", int(independent_ops))
    c3.metric("Elmina operator count", elmina_count)

    if elmina_count < 3:
        st.warning("⚠️ Elmina coverage may be incomplete. Recommend supplementing with manual Google Maps / Facebook search for 'taska Kota Elmina' and 'tadika Elmina Shah Alam'.")

    st.info(f"Data drawn from {len(found_sources)} of 5 sources: {', '.join(found_sources) if found_sources else 'None'}")

    chart_df = (
        df_comp.assign(_fee=df_comp["fee_fullday_raw"].astype(str).map(parse_fee_value))
        .query("_fee > 0")
        .groupby("neighbourhood", as_index=False)["_fee"]
        .mean()
    )
    if not chart_df.empty:
        fig = px.bar(chart_df, x="neighbourhood", y="_fee", title="Average Full-Day Fee by Neighbourhood")
        fig.add_hline(y=TARGET_B_FEE, line_dash="dash", annotation_text="Target Brand B")
        st.plotly_chart(fig, use_container_width=True)

    st.warning(
        "⚠️ This is a draft framework only. The statistics above are auto-filled. "
        "You must write the actual analysis. Do not summarise the table — interpret what it means. "
        "Be direct. Have an opinion. Max 1 page (~400 words)."
    )

    author_name = st.text_input("From:", value="[Your Name]")

    sec_a = st.text_area("a) What did you find?", value="Prompt: quantify total operators, cluster patterns, and saturation by area.")
    sec_b = st.text_area("b) Biggest threats to the Target", value="Prompt: identify 2–3 specific operators and explain why each is a threat to Brand A or Brand B.")
    sec_c = st.text_area("c) Anything surprising?", value="Prompt: highlight one unexpected insight (e.g., Elmina gap, unusual pricing, hidden competitor).")
    sec_d = st.text_area("d) Overall read", value="Prompt: give a direct verdict — manageable or daunting, and for which brand.")

    memo = f"""MEMORANDUM
To:    The Team, Newmoon Capital
From:  {author_name}
Re:    Project Kestrel — Competitive Landscape, Shah Alam ECE Corridor
Date:  {date.today().isoformat()}

a) What did you find?
{sec_a}

b) Biggest threats to the Target
{sec_b}

c) Anything surprising?
{sec_c}

d) Overall read
{sec_d}
"""

    st.code(memo)
    st.download_button("Download memo as .txt", data=memo.encode("utf-8"), file_name="memo_draft.txt", mime="text/plain")


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
                row = {**new_data, "source_primary": "Manual Entry", "is_moe_registered": False, "threat_score": "", "lat": "", "lng": ""}
                df_new = pd.concat([df_all, pd.DataFrame([row])], ignore_index=True)
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
        del_note = str(df_all.loc[del_idx, "source_notes"])
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
            st.dataframe(up_df.head(30), use_container_width=True)
            if st.button("Confirm import"):
                up_df["source_primary"] = "Manual Entry"
                up_df["is_moe_registered"] = False
                up_df["threat_score"] = ""
                up_df["lat"] = ""
                up_df["lng"] = ""
                combined = pd.concat([df_all, up_df], ignore_index=True)
                combined = combined.drop_duplicates(subset=["centre_name", "address"], keep="first")
                save_data(combined)
                st.success("Bulk import complete.")
                st.rerun()


def main():
    st.title("Project Kestrel — ECE Market Intelligence Dashboard")

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
        map_df = pd.concat([df_targets, df_competitors], ignore_index=True)
        render_tab2(map_df, radius_km, st.session_state["resolved_lat"], st.session_state["resolved_lng"])

    with t3:
        render_tab3(df_competitors)

    with t4:
        render_tab4(df)


if __name__ == "__main__":
    main()
