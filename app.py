"""
Metro Market Ranker — Streamlit dashboard.

Reads the CSVs produced by backend/main.py and lets the user explore
rankings interactively:

- Pick an investment thesis (balanced / yield / growth / affordability / contrarian)
- Adjust how many markets to display
- Search for a specific market by name
- See a ranked table, a score bar chart, and a US state-level heatmap
- Download the filtered results as CSV

Run:
    pip install streamlit plotly pandas
    py backend/main.py          # (first, to generate data/metro_markets_balanced.csv)
    streamlit run app.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

# Make backend.main importable regardless of where streamlit is launched from
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.main import THESES, apply_thesis  # noqa: E402


# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Metro Market Ranker",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("Metro Market Ranker")
st.caption(
    "US real estate markets ranked by investment thesis. "
    "Built from public Zillow rent/home-value data, Census metro population growth, "
    "and (when available) Census ACS income growth."
)


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

DATA_FILE = PROJECT_ROOT / "data" / "metro_markets_balanced.csv"


@st.cache_data(show_spinner=False)
def load_data(path: Path) -> tuple[pd.DataFrame, float]:
    """Load the balanced CSV (which contains all markets, unfiltered by thesis)."""
    df = pd.read_csv(path)
    mtime = os.path.getmtime(path)
    return df, mtime


if not DATA_FILE.exists():
    st.error(
        "No data file found at `data/metro_markets_balanced.csv`. "
        "Run `py backend/main.py` first to generate it."
    )
    st.stop()

df, data_mtime = load_data(DATA_FILE)
last_refresh = pd.to_datetime(data_mtime, unit="s").tz_localize("UTC").tz_convert("US/Eastern")

# Income availability: treat column-is-all-zero as "not available" since our
# fallback fills NaNs with the median (which will be 0 if the whole column
# was missing).
income_available = (
    "income_growth_annualized_pct" in df.columns
    and df["income_growth_annualized_pct"].fillna(0).abs().sum() > 0
)


# ---------------------------------------------------------------------------
# Sidebar controls
# ---------------------------------------------------------------------------

st.sidebar.header("Controls")

thesis_options = list(THESES.keys())
thesis_name = st.sidebar.selectbox(
    "Investment thesis",
    options=thesis_options,
    index=thesis_options.index("balanced"),
    format_func=lambda x: x.title(),
    help="Each thesis applies different filters and sorts by a different metric.",
)

top_n = st.sidebar.slider("Top N markets to display", 5, 50, 15)

search_term = st.sidebar.text_input(
    "Search for a market",
    placeholder="e.g. Syracuse, Austin, TX",
    help="Filter by market name. Works alongside the thesis filter.",
)

st.sidebar.markdown("---")
st.sidebar.caption(f"Data last refreshed: {last_refresh.strftime('%Y-%m-%d %H:%M %Z')}")
st.sidebar.caption(
    "To refresh, run `py backend/main.py` in the project root, then reload this page."
)


# ---------------------------------------------------------------------------
# Apply thesis, then search
# ---------------------------------------------------------------------------

thesis = THESES[thesis_name]
filtered = apply_thesis(df, thesis_name, thesis, income_available)

if search_term:
    filtered = filtered[
        filtered["market_display"].str.contains(search_term, case=False, na=False)
    ]


# ---------------------------------------------------------------------------
# Top section: thesis summary + headline metrics
# ---------------------------------------------------------------------------

col_desc, col_metrics = st.columns([3, 2])

with col_desc:
    st.subheader(f"{thesis_name.title()} Thesis")
    st.write(thesis["description"])

    if thesis["filters"]:
        filter_parts = [
            f"`{col} {op} {threshold}`"
            for col, (op, threshold) in thesis["filters"].items()
        ]
        st.markdown("**Filters applied:** " + ", ".join(filter_parts))
    else:
        st.markdown("**Filters applied:** none (balanced view across all markets)")

    st.markdown(f"**Sorted by:** `{thesis['sort_by']}`")

    if search_term:
        st.markdown(f"**Search:** `{search_term}`")

    if thesis.get("requires_income") and not income_available:
        st.warning(
            "This thesis requires income data, which is not available in the "
            "current dataset (Census ACS API was unreachable when data was "
            "last refreshed). Rerun `backend/main.py` once the API is back."
        )

with col_metrics:
    n_total = len(df)
    n_match = len(filtered)
    label = "Markets matching search" if search_term else "Markets matching thesis"
    st.metric(label, f"{n_match} / {n_total}")
    if n_match > 0:
        st.metric("Median rent growth", f"{filtered['rent_growth_pct'].median():.1f}%")
        st.metric("Median gross yield", f"{filtered['rent_to_value'].median() * 100:.2f}%")

st.markdown("---")


# ---------------------------------------------------------------------------
# No-match guard
# ---------------------------------------------------------------------------

if len(filtered) == 0:
    if search_term:
        st.info(
            f"No markets matching '{search_term}' in the {thesis_name} thesis. "
            "Try clearing the search, changing thesis, or checking spelling."
        )
    else:
        st.info(
            "No markets match the current thesis filters. "
            "Either this thesis is correctly telling you that no metro meets its "
            "criteria right now, or the thresholds are too tight. Loosen them in "
            "`backend/main.py` inside the `THESES` dict and rerun."
        )
    st.stop()


# ---------------------------------------------------------------------------
# Ranked table
# ---------------------------------------------------------------------------

if search_term:
    st.subheader(f"Search results for '{search_term}' ({len(filtered)} markets)")
else:
    st.subheader(f"Top {min(top_n, len(filtered))} Markets")

display_cols_base = [
    "market_display",
    "rent_latest",
    "rent_growth_pct",
    "home_value_latest",
    "home_value_growth_pct",
    "population_growth_pct",
    "rent_to_value",
]
if income_available:
    display_cols_base.insert(-1, "income_growth_annualized_pct")

display_cols_base.extend(["growth_score", "value_score", "balanced_score"])

# Add the thesis's sort column if it's not already in the display
sort_col = thesis["sort_by"]
if sort_col not in display_cols_base and sort_col in filtered.columns:
    display_cols_base.append(sort_col)

display = filtered[display_cols_base].head(top_n).copy()
if "rent_to_value" in display.columns:
    display["rent_to_value"] = display["rent_to_value"] * 100
    
# Nicer column labels
label_map = {
    "market_display": "Market",
    "rent_latest": "Rent ($/mo)",
    "rent_growth_pct": "Rent YoY %",
    "home_value_latest": "Home Value ($)",
    "home_value_growth_pct": "Home Value YoY %",
    "population_growth_pct": "Pop Growth %",
    "income_growth_annualized_pct": "Income Growth % (annualized)",
    "rent_to_value": "Gross Yield",
    "affordability_gap": "Affordability Gap",
    "growth_score": "Growth Score",
    "value_score": "Value Score",
    "balanced_score": "Balanced Score",
}
display = display.rename(columns={c: label_map.get(c, c) for c in display.columns})

st.dataframe(
    display,
    use_container_width=True,
    hide_index=True,
    column_config={
        "Rent ($/mo)": st.column_config.NumberColumn(format="$%,.0f"),
        "Home Value ($)": st.column_config.NumberColumn(format="$%,.0f"),
        "Gross Yield": st.column_config.NumberColumn(format="%.2f%%"),
        "Rent YoY %": st.column_config.NumberColumn(format="%.2f%%"),
        "Home Value YoY %": st.column_config.NumberColumn(format="%.2f%%"),
        "Pop Growth %": st.column_config.NumberColumn(format="%.2f%%"),
        "Income Growth % (annualized)": st.column_config.NumberColumn(format="%.2f%%"),
        "Growth Score": st.column_config.NumberColumn(format="%.2f"),
        "Value Score": st.column_config.NumberColumn(format="%.2f"),
        "Balanced Score": st.column_config.NumberColumn(format="%.2f"),
    },
)


# ---------------------------------------------------------------------------
# Visuals
# ---------------------------------------------------------------------------

col_bar, col_map = st.columns(2)

with col_bar:
    st.subheader("Ranking chart")
    bar_df = filtered.head(top_n).copy()
    sort_col_in_df = sort_col if sort_col in bar_df.columns else "balanced_score"
    fig_bar = px.bar(
        bar_df.sort_values(sort_col_in_df, ascending=True),
        y="market_display",
        x=sort_col_in_df,
        orientation="h",
        labels={"market_display": "", sort_col_in_df: label_map.get(sort_col_in_df, sort_col_in_df)},
        height=max(400, 22 * len(bar_df)),
    )
    fig_bar.update_layout(margin=dict(l=120, r=20, t=20, b=40))
    st.plotly_chart(fig_bar, use_container_width=True)


def extract_state(market_name: str) -> str | None:
    """'Syracuse, NY' -> 'NY'"""
    if not isinstance(market_name, str) or "," not in market_name:
        return None
    return market_name.split(",")[-1].strip().split(" ")[0][:2].upper()


with col_map:
    st.subheader("State heatmap")
    map_df = filtered.copy()
    map_df["state"] = map_df["market_display"].apply(extract_state)
    state_agg = (
        map_df.dropna(subset=["state"])
        .groupby("state")[sort_col_in_df]
        .mean()
        .reset_index()
    )

    if len(state_agg) > 0:
        fig_map = px.choropleth(
            state_agg,
            locations="state",
            locationmode="USA-states",
            color=sort_col_in_df,
            scope="usa",
            color_continuous_scale="Viridis",
            labels={sort_col_in_df: label_map.get(sort_col_in_df, sort_col_in_df)},
        )
        fig_map.update_layout(margin=dict(l=0, r=0, t=0, b=0))
        st.plotly_chart(fig_map, use_container_width=True)
    else:
        st.info("No state data extractable from current selection.")


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

st.markdown("---")
st.download_button(
    label="Download filtered markets as CSV",
    data=filtered.to_csv(index=False).encode("utf-8"),
    file_name=f"metro_markets_{thesis_name}.csv",
    mime="text/csv",
)

st.caption(
    "Data sources: Zillow Research (ZORI, ZHVI), US Census Bureau (CBSA "
    "Population Estimates, ACS 5-Year Median Household Income). "
    "Scoring weights are illustrative — not financial advice."
)