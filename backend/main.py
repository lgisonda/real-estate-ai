import os
import pandas as pd


def main():
    os.makedirs("data", exist_ok=True)

    url = "https://raw.githubusercontent.com/jakevdp/data-USstates/master/state-population.csv"

    df = pd.read_csv(url)
    df = df[df["ages"] == "total"]
    df = df.dropna(subset=["population"])

    start_year = 2010
    end_year = df["year"].max()

    pop_start = df[df["year"] == start_year][["state/region", "population"]]
    pop_end = df[df["year"] == end_year][["state/region", "population"]]

    pop_start = pop_start.rename(columns={"population": "population_start"})
    pop_end = pop_end.rename(columns={"population": "population_end"})

    merged = pop_start.merge(pop_end, on="state/region")

    merged["population_growth_pct"] = (
        (merged["population_end"] - merged["population_start"])
        / merged["population_start"]
    ) * 100

    result = merged.rename(columns={"state/region": "market"})
    result = result.sort_values("population_growth_pct", ascending=False)

    output_path = "data/population_growth.csv"
    result.to_csv(output_path, index=False)

    print("\nTop 10 Markets by Population Growth:\n")
    print(result.head(10))


if __name__ == "__main__":
    main()
    