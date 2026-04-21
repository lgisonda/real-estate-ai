import os
import pandas as pd


def get_population_data():
    url = "https://raw.githubusercontent.com/jakevdp/data-USstates/master/state-population.csv"

    df = pd.read_csv(url)
    df = df[df["ages"] == "total"].copy()
    df = df.dropna(subset=["population"])

    start_year = 2010
    end_year = df["year"].max()

    pop_start = (
        df[df["year"] == start_year][["state/region", "population"]]
        .rename(columns={"population": "population_start"})
    )

    pop_end = (
        df[df["year"] == end_year][["state/region", "population"]]
        .rename(columns={"population": "population_end"})
    )

    merged = pop_start.merge(pop_end, on="state/region", how="inner")

    merged["population_growth_pct"] = (
        (merged["population_end"] - merged["population_start"])
        / merged["population_start"]
    ) * 100

    return merged.rename(columns={"state/region": "market"})


def get_mock_rent_data():
    rent_data = pd.DataFrame(
        {
            "market": ["TX", "FL", "AZ", "CO", "UT", "WA", "NC", "TN", "GA", "NV"],
            "rent_growth_pct": [12.4, 10.1, 9.8, 8.7, 11.2, 7.9, 9.1, 8.4, 7.8, 10.5],
        }
    )
    return rent_data


def main():
    os.makedirs("data", exist_ok=True)

    pop = get_population_data()
    rent = get_mock_rent_data()

    combined = pop.merge(rent, on="market", how="inner").copy()

    combined["score"] = (
        combined["population_growth_pct"] * 0.5
        + combined["rent_growth_pct"] * 0.5
    )

    combined = combined.sort_values("score", ascending=False)

    output_path = "data/market_scores.csv"
    combined.to_csv(output_path, index=False)

    print("\nTop Markets (Combined Score):\n")
    print(
        combined[
            ["market", "population_growth_pct", "rent_growth_pct", "score"]
        ].head(10).to_string(index=False)
    )

    print(f"\nSaved to {output_path}")


if __name__ == "__main__":
    main()