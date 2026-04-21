"""
Metro market ranker: combines Zillow rent (ZORI), Zillow home value (ZHVI),
and Census metro population growth into a simple growth/value/balanced score.

Changes from the previous version (picking up where the ChatGPT debug left off):

1. get_metro_population_data() no longer relies on `LSAD == 1`.
   The actual Census CBSA file stores LSAD as strings ("M1" for Metro,
   "M2" for Micro, "M6" for Metropolitan Division), which is why the old
   filter returned 0 rows. The new function:
     - tries the correct LSAD code ("M1")
     - falls back to a text match on "Metropolitan Statistical Area" in NAME
     - prints diagnostics (columns, LSAD value counts, sample names)
       so debugging the next failure is fast
2. Uses the metro (CBSA) totals file, not the state (NST) file.
   Those are two different datasets — state totals won't join to Zillow
   metro rows.
3. All merges are outer/left-safe so Zillow rows survive even when the
   Census side has a partial match.
4. Added get_metro_income_data() using Census ACS 5-Year API to pull
   metro median household income and compute annualized 5-year growth.
5. Re-weighted scores to include income growth as a fourth signal.

Run:
    py backend/main.py
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
import urllib.error
import urllib.request

import pandas as pd


# ---------------------------------------------------------------------------
# Investment theses (presets)
# ---------------------------------------------------------------------------
# Each thesis is a filter + sort combo that expresses a specific investor
# viewpoint. Run with `--thesis <name>` to apply one.
#
# Filter syntax: {column: (op, threshold)} where op is one of >, >=, <, <=, ==.

THESES: dict[str, dict] = {
    "balanced": {
        "description": "Balanced view across growth, yield, and demand signals",
        "filters": {},
        "sort_by": "balanced_score",
    },
    "yield": {
        "description": "Income-producing rentals: high cap rate, not declining",
        "filters": {
            "rent_to_value": (">", 0.07),          # 7%+ gross yield
            "population_growth_pct": (">", -0.5),  # market not dying
            "rent_growth_pct": (">", 0),           # rents not falling
        },
        "sort_by": "rent_to_value",
    },
    "growth": {
        "description": "Appreciation + rent escalation: Sun Belt / Mountain West story",
        "filters": {
            "population_growth_pct": (">", 2),
            "rent_growth_pct": (">", 3),
        },
        "sort_by": "growth_score",
    },
    "affordability": {
        "description": "Income growing faster than home prices — runway for future appreciation",
        "filters": {
            "income_growth_annualized_pct": (">", 2),
        },
        "sort_by": "affordability_gap",
        "requires_income": True,
    },
    "contrarian": {
        "description": "Beat-up markets with compelling yield — mean-reversion bet",
        "filters": {
            "home_value_growth_pct": ("<", 0),
            "rent_to_value": (">", 0.08),
        },
        "sort_by": "rent_to_value",
    },
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def normalize_metro_name(name: str) -> str:
    """Normalize a metro name for merging across datasets."""
    if not isinstance(name, str):
        return ""

    name = name.lower().strip()

    replacements = [
        " metropolitan statistical area",
        " micropolitan statistical area",
        " metropolitan division",
        " metro area",
    ]
    for r in replacements:
        name = name.replace(r, "")

    # Keep only text before first comma
    name = name.split(",")[0]

    # Keep first city cluster before hyphen
    name = name.split("-")[0]

    # Remove punctuation and collapse whitespace
    name = re.sub(r"[^a-z0-9\s]", " ", name)
    name = re.sub(r"\s+", " ", name).strip()

    return name


# ---------------------------------------------------------------------------
# Zillow: rent (ZORI)
# ---------------------------------------------------------------------------

def get_metro_rent_data() -> pd.DataFrame:
    url = (
        "https://files.zillowstatic.com/research/public_csvs/zori/"
        "Metro_zori_uc_sfrcondomfr_sm_month.csv"
    )
    df = pd.read_csv(url).copy()

    date_cols = [c for c in df.columns if str(c)[:4].isdigit()]
    if len(date_cols) < 13:
        raise ValueError("Not enough monthly columns in rent data.")

    latest_col = date_cols[-1]
    prior_year_col = date_cols[-13]

    cols = ["RegionName", latest_col, prior_year_col]
    if "SizeRank" in df.columns:
        cols.append("SizeRank")

    metro = df[cols].copy()
    metro = metro.rename(
        columns={
            "RegionName": "market",
            latest_col: "rent_latest",
            prior_year_col: "rent_12mo_ago",
            "SizeRank": "size_rank",
        }
    )

    metro = metro.dropna(subset=["rent_latest", "rent_12mo_ago"]).copy()

    metro["rent_growth_pct"] = (
        (metro["rent_latest"] - metro["rent_12mo_ago"])
        / metro["rent_12mo_ago"]
    ) * 100

    metro["metro_key"] = metro["market"].apply(normalize_metro_name)
    metro = metro[metro["metro_key"] != ""].copy()
    metro = metro.sort_values("rent_latest", ascending=False)
    metro = metro.drop_duplicates(subset=["metro_key"]).copy()

    return metro


# ---------------------------------------------------------------------------
# Zillow: home value (ZHVI)
# ---------------------------------------------------------------------------

def get_metro_home_value_data() -> pd.DataFrame:
    url = (
        "https://files.zillowstatic.com/research/public_csvs/zhvi/"
        "Metro_zhvi_uc_sfrcondo_tier_0.33_0.67_sm_sa_month.csv"
    )
    df = pd.read_csv(url).copy()

    date_cols = [c for c in df.columns if str(c)[:4].isdigit()]
    if len(date_cols) < 13:
        raise ValueError("Not enough monthly columns in home value data.")

    latest_col = date_cols[-1]
    prior_year_col = date_cols[-13]

    cols = ["RegionName", latest_col, prior_year_col]
    if "SizeRank" in df.columns:
        cols.append("SizeRank")

    metro = df[cols].copy()
    metro = metro.rename(
        columns={
            "RegionName": "market",
            latest_col: "home_value_latest",
            prior_year_col: "home_value_12mo_ago",
            "SizeRank": "size_rank_hv",
        }
    )

    metro = metro.dropna(subset=["home_value_latest", "home_value_12mo_ago"]).copy()

    metro["home_value_growth_pct"] = (
        (metro["home_value_latest"] - metro["home_value_12mo_ago"])
        / metro["home_value_12mo_ago"]
    ) * 100

    metro["metro_key"] = metro["market"].apply(normalize_metro_name)
    metro = metro[metro["metro_key"] != ""].copy()
    metro = metro.sort_values("home_value_latest", ascending=False)
    metro = metro.drop_duplicates(subset=["metro_key"]).copy()

    return metro


# ---------------------------------------------------------------------------
# Census: metro-level (CBSA) population growth — FIXED
# ---------------------------------------------------------------------------

def get_metro_population_data() -> pd.DataFrame:
    """
    Pulls the Census CBSA population file and returns a DataFrame keyed by
    normalized metro name. Resilient to the two common failure modes from
    the previous version:
      - LSAD stored as strings ("M1"/"M2") rather than ints
      - Column names drifting between vintages (e.g. POPESTIMATE2024 vs 2023)
    """
    url = (
        "https://www2.census.gov/programs-surveys/popest/datasets/"
        "2020-2023/metro/totals/cbsa-est2023-alldata.csv"
    )
    df = pd.read_csv(url, encoding="latin1", low_memory=False).copy()

    print("\nCensus columns (first 25):")
    print(df.columns.tolist()[:25])

    if "LSAD" in df.columns:
        print("\nLSAD value counts:")
        print(df["LSAD"].value_counts(dropna=False).head(10))

    # --- Find the name column -------------------------------------------------
    name_col = None
    for col in ["NAME", "CBSA_TITLE", "STNAME"]:
        if col in df.columns:
            name_col = col
            break
    if name_col is None:
        raise ValueError("Could not find metro name column in Census file.")

    # --- Find population columns ---------------------------------------------
    start_col = None
    for col in ["POPESTIMATE2020", "CENSUS2020POP", "ESTIMATESBASE2020"]:
        if col in df.columns:
            start_col = col
            break
    if start_col is None:
        raise ValueError("Could not find starting population column in Census file.")

    end_col = None
    for col in ["POPESTIMATE2024", "POPESTIMATE2023", "POPESTIMATE2022"]:
        if col in df.columns:
            end_col = col
            break
    if end_col is None:
        raise ValueError("Could not find ending population column in Census file.")

    print(f"\nUsing name={name_col}, start={start_col}, end={end_col}")

    # --- Filter to Metropolitan Statistical Areas only -----------------------
    # Prefer LSAD == "M1" (MSA). Fall back to text match if the column's not
    # there or all M1 rows got filtered out.
    filtered = df
    if "LSAD" in df.columns:
        mask_m1 = df["LSAD"].astype(str).str.strip() == "M1"
        if mask_m1.sum() > 0:
            filtered = df[mask_m1].copy()
            print(f"Filtered by LSAD=='M1': {len(filtered)} rows")

    if len(filtered) == 0 or "LSAD" not in df.columns:
        mask_text = df[name_col].astype(str).str.contains(
            "Metropolitan Statistical Area", case=False, na=False
        )
        filtered = df[mask_text].copy()
        print(f"Fallback text filter on NAME: {len(filtered)} rows")

    if len(filtered) == 0:
        print("\nSample NAME values to debug:")
        print(df[name_col].head(10).tolist())
        raise ValueError("No MSA rows found in Census file.")

    pop = filtered[[name_col, start_col, end_col]].copy()
    pop = pop.rename(
        columns={
            name_col: "census_market",
            start_col: "population_start",
            end_col: "population_end",
        }
    )

    pop = pop.dropna(subset=["population_start", "population_end"]).copy()
    pop["population_growth_pct"] = (
        (pop["population_end"] - pop["population_start"]) / pop["population_start"]
    ) * 100

    pop["metro_key"] = pop["census_market"].apply(normalize_metro_name)
    pop = pop[pop["metro_key"] != ""].copy()

    pop = pop.sort_values("population_end", ascending=False)
    pop = pop.drop_duplicates(subset=["metro_key"]).copy()

    return pop[["metro_key", "census_market", "population_growth_pct", "population_end"]]


# ---------------------------------------------------------------------------
# Census ACS: metro median household income + growth
# ---------------------------------------------------------------------------

def _fetch_acs_income(year: int, retries: int = 3, backoff: float = 2.0) -> pd.DataFrame:
    """Fetch ACS 5-Year median household income for all MSAs/microsMSAs."""
    url = (
        f"https://api.census.gov/data/{year}/acs/acs5"
        "?get=NAME,B19013_001E"
        "&for=metropolitan+statistical+area/micropolitan+statistical+area:*"
    )

    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "metro-market-ranker/1.0"},
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            break
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
            last_err = e
            if attempt < retries:
                wait = backoff ** attempt
                print(f"    attempt {attempt} failed ({e}); retrying in {wait:.1f}s")
                time.sleep(wait)
            else:
                raise last_err

    header, *rows = payload
    df = pd.DataFrame(rows, columns=header)
    df = df.rename(columns={"NAME": "acs_market", "B19013_001E": f"income_{year}"})
    df[f"income_{year}"] = pd.to_numeric(df[f"income_{year}"], errors="coerce")
    # The geo column has a wordy name; rename to "cbsa_code"
    geo_col = [c for c in df.columns if "metropolitan" in c.lower()][0]
    df = df.rename(columns={geo_col: "cbsa_code"})
    return df[["cbsa_code", "acs_market", f"income_{year}"]]


def get_metro_income_data() -> pd.DataFrame:
    """
    Pulls metro median household income for a recent 5-year pair from
    ACS 5-Year Estimates and computes annualized growth.

    Tries year pairs newest-first and falls back if a vintage isn't
    released yet.
    """
    # End year, start year (5-year span)
    year_pairs = [(2024, 2019), (2023, 2018), (2022, 2017)]

    for end_year, start_year in year_pairs:
        try:
            print(f"\nFetching ACS income {start_year} -> {end_year}...")
            latest = _fetch_acs_income(end_year)
            prior = _fetch_acs_income(start_year)

            merged = latest.merge(
                prior[["cbsa_code", f"income_{start_year}"]],
                on="cbsa_code",
                how="inner",
            )
            merged = merged.rename(
                columns={
                    f"income_{end_year}": "income_latest",
                    f"income_{start_year}": "income_prior",
                }
            )
            merged = merged.dropna(subset=["income_latest", "income_prior"])
            merged = merged[merged["income_prior"] > 0].copy()

            n_years = end_year - start_year
            merged["income_growth_annualized_pct"] = (
                (merged["income_latest"] / merged["income_prior"]) ** (1 / n_years) - 1
            ) * 100

            merged["metro_key"] = merged["acs_market"].apply(normalize_metro_name)
            merged = merged[merged["metro_key"] != ""]
            merged = merged.sort_values("income_latest", ascending=False)
            merged = merged.drop_duplicates(subset=["metro_key"]).copy()

            print(
                f"  {len(merged)} metros with income for "
                f"{start_year}-{end_year}"
            )
            return merged[
                ["metro_key", "acs_market", "income_latest", "income_growth_annualized_pct"]
            ]

        except Exception as e:
            print(f"  Failed {start_year}->{end_year}: {e}")
            continue

    raise RuntimeError("Could not fetch ACS income data from any year pair.")


# ---------------------------------------------------------------------------
# Thesis application
# ---------------------------------------------------------------------------

_OPS = {
    ">": lambda s, t: s > t,
    ">=": lambda s, t: s >= t,
    "<": lambda s, t: s < t,
    "<=": lambda s, t: s <= t,
    "==": lambda s, t: s == t,
}


def apply_thesis(
    df: pd.DataFrame,
    thesis_name: str,
    thesis: dict,
    income_available: bool,
) -> pd.DataFrame:
    """Filter and sort a dataframe according to a named thesis preset."""
    if thesis.get("requires_income") and not income_available:
        print(
            f"[WARN] Thesis '{thesis_name}' requires income data, "
            "which is unavailable. Returning empty frame."
        )
        return df.iloc[0:0].copy()

    result = df.copy()

    # Always compute affordability_gap so sort_by="affordability_gap" works
    if {"income_growth_annualized_pct", "home_value_growth_pct"}.issubset(result.columns):
        result["affordability_gap"] = (
            result["income_growth_annualized_pct"] - result["home_value_growth_pct"]
        )

    # Apply filters
    for col, (op, threshold) in thesis["filters"].items():
        if col not in result.columns:
            print(f"[WARN] Filter column '{col}' missing; skipping")
            continue
        mask = _OPS[op](result[col], threshold)
        result = result[mask].copy()

    # Sort
    sort_col = thesis["sort_by"]
    if sort_col not in result.columns:
        print(f"[WARN] Sort column '{sort_col}' missing; falling back to balanced_score")
        sort_col = "balanced_score"
    result = result.sort_values(sort_col, ascending=False)

    return result


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def main(
    thesis_name: str = "balanced",
    top_n_by_size: int = 100,
    show_all_theses: bool = False,
) -> None:
    """
    top_n_by_size: only rank the N largest metros by Zillow SizeRank.
    Default 100 keeps the results to real markets and kills small-sample
    noise from college towns and rural micropolitans.
    """
    os.makedirs("data", exist_ok=True)

    rent = get_metro_rent_data()
    home = get_metro_home_value_data()
    pop = get_metro_population_data()

    try:
        income = get_metro_income_data()
        income_available = True
    except Exception as e:
        print(f"\n[WARN] Income fetch failed: {e}")
        print("[WARN] Continuing without income data; scores will use 3 signals.")
        income = None
        income_available = False

    print(f"\nRent rows: {len(rent)}")
    print(f"Home value rows: {len(home)}")
    print(f"Population rows: {len(pop)}")
    if income_available:
        print(f"Income rows: {len(income)}")

    combined = rent.merge(
        home,
        on="metro_key",
        how="inner",
        suffixes=("_rent", "_home"),
    ).copy()
    print(f"After rent/home merge: {len(combined)}")

    combined = combined.merge(pop, on="metro_key", how="left").copy()
    print(f"After adding population: {len(combined)}")
    print(
        f"Rows with population match: "
        f"{combined['population_growth_pct'].notna().sum()}"
    )

    if income_available:
        combined = combined.merge(income, on="metro_key", how="left").copy()
        print(f"After adding income: {len(combined)}")
        print(
            f"Rows with income match: "
            f"{combined['income_growth_annualized_pct'].notna().sum()}"
        )
    else:
        # Create an empty column so downstream scoring doesn't crash
        combined["income_growth_annualized_pct"] = pd.NA
        combined["income_latest"] = pd.NA

    # --- Filter to largest metros to kill small-sample noise -----------------
    if "size_rank" in combined.columns and combined["size_rank"].notna().any():
        before = len(combined)
        combined = combined[combined["size_rank"] <= top_n_by_size].copy()
        print(
            f"Filtered to top {top_n_by_size} by SizeRank: "
            f"{before} -> {len(combined)} rows"
        )

    # Prefer rent market name for display
    combined["market_display"] = combined["market_rent"]

    if "size_rank" not in combined.columns:
        combined["size_rank"] = combined.get("size_rank_hv")

    combined["rent_to_value"] = (
        combined["rent_latest"] * 12
    ) / combined["home_value_latest"]

    # Fill missing population/income with median rather than dropping rows
    for col in ["population_growth_pct", "income_growth_annualized_pct"]:
        fill = combined[col].median() if combined[col].notna().any() else 0.0
        combined[col] = combined[col].fillna(fill)

    # --- Scores --------------------------------------------------------------
    if income_available:
        # 4-signal growth + yield
        combined["growth_score"] = (
            combined["rent_growth_pct"] * 0.30
            + combined["home_value_growth_pct"] * 0.20
            + combined["population_growth_pct"] * 0.25
            + combined["income_growth_annualized_pct"] * 0.25
        )
        combined["value_score"] = (
            combined["rent_to_value"] * 100 * 0.45
            + combined["rent_growth_pct"] * 0.15
            + combined["home_value_growth_pct"] * 0.10
            + combined["population_growth_pct"] * 0.15
            + combined["income_growth_annualized_pct"] * 0.15
        )
        combined["balanced_score"] = (
            combined["rent_growth_pct"] * 0.25
            + combined["home_value_growth_pct"] * 0.20
            + combined["population_growth_pct"] * 0.20
            + combined["income_growth_annualized_pct"] * 0.20
            + combined["rent_to_value"] * 100 * 0.15
        )
    else:
        # 3-signal fallback (income unavailable)
        combined["growth_score"] = (
            combined["rent_growth_pct"] * 0.4
            + combined["home_value_growth_pct"] * 0.3
            + combined["population_growth_pct"] * 0.3
        )
        combined["value_score"] = (
            combined["rent_to_value"] * 100 * 0.5
            + combined["rent_growth_pct"] * 0.2
            + combined["home_value_growth_pct"] * 0.1
            + combined["population_growth_pct"] * 0.2
        )
        combined["balanced_score"] = (
            combined["rent_growth_pct"] * 0.3
            + combined["home_value_growth_pct"] * 0.25
            + combined["population_growth_pct"] * 0.25
            + combined["rent_to_value"] * 100 * 0.2
        )

    display_cols = [
        "market_display",
        "rent_latest",
        "rent_growth_pct",
        "home_value_latest",
        "home_value_growth_pct",
        "population_growth_pct",
        "income_growth_annualized_pct",
        "rent_to_value",
        "growth_score",
        "value_score",
        "balanced_score",
    ]

    theses_to_run = list(THESES.keys()) if show_all_theses else [thesis_name]

    for name in theses_to_run:
        if name not in THESES:
            print(f"\n[ERROR] Unknown thesis '{name}'. Options: {list(THESES.keys())}")
            continue

        thesis = THESES[name]
        filtered = apply_thesis(combined, name, thesis, income_available)

        output_path = f"data/metro_markets_{name}.csv"
        filtered.to_csv(output_path, index=False)

        print("\n" + "=" * 72)
        print(f"Thesis: {name.upper()}")
        print(f"  {thesis['description']}")
        filter_desc = (
            ", ".join(f"{c} {op} {t}" for c, (op, t) in thesis["filters"].items())
            or "(no filters)"
        )
        print(f"  Filters: {filter_desc}")
        print(f"  Sort by: {thesis['sort_by']}")
        print(f"  {len(filtered)} markets match")
        print("=" * 72)

        if len(filtered) == 0:
            print("(no metros match this thesis's filters)")
            continue

        # Add affordability_gap to display if the thesis uses it
        cols = list(display_cols)
        if thesis["sort_by"] == "affordability_gap" and "affordability_gap" in filtered.columns:
            cols.insert(-3, "affordability_gap")

        top_n = min(15, len(filtered))
        print(f"\nTop {top_n}:\n")
        print(filtered[cols].head(top_n).to_string(index=False))
        print(f"\nSaved to {output_path}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rank US metro real-estate markets by a chosen investment thesis.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Theses:\n"
            + "\n".join(
                f"  {name:<14} {spec['description']}" for name, spec in THESES.items()
            )
        ),
    )
    parser.add_argument(
        "--thesis",
        default="balanced",
        choices=list(THESES.keys()),
        help="Investment thesis preset to apply (default: balanced)",
    )
    parser.add_argument(
        "--top-n-by-size",
        type=int,
        default=100,
        help="Keep only the top N metros by Zillow SizeRank (default: 100)",
    )
    parser.add_argument(
        "--all-theses",
        action="store_true",
        help="Run every thesis and save each result to its own CSV",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    main(
        thesis_name=args.thesis,
        top_n_by_size=args.top_n_by_size,
        show_all_theses=args.all_theses,
    )