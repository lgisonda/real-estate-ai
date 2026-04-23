# Metro Market Ranker

**Live demo:** https://metro-market-ranker.streamlit.app/

An interactive dashboard that ranks US real estate metros by investment thesis, combining Zillow rent and home-value data with Census population and income growth.

![Dashboard](dashboard.png)

## What it does

Pulls four public datasets, normalizes them to a common metro (CBSA) key, scores every market on growth / value / balanced composites, and lets you filter by investment thesis:

- **Balanced** — default view across all signals
- **Yield** — high cashflow markets (gross yield > 7%)
- **Growth** — population and rent growth leaders
- **Affordability** — rising incomes and reasonable prices
- **Contrarian** — discounted home values with decent yield

## Data sources

- **Zillow Research** — ZORI (Zillow Observed Rent Index) and ZHVI (Zillow Home Value Index), monthly metro-level
- **US Census Bureau** — CBSA Population Estimates (vintage 2023)
- **US Census ACS 5-Year** — Median household income (B19013_001E), 2019 vs 2024

All sources are public and no API keys are required.

## How it works

The project has two components: a backend data pipeline and a Streamlit dashboard.

### Backend pipeline (`backend/main.py`)

1. Downloads Zillow ZORI (rent) and ZHVI (home value) CSVs from Zillow Research
2. Downloads the Census CBSA Population Estimates CSV (vintage 2023)
3. Calls the Census ACS 5-Year API to get median household income for 2019 and 2024
4. Normalizes metro names to a common CBSA key — strips "Metropolitan Statistical Area" suffixes, punctuation, and multi-city name variants so "Syracuse, NY" matches across all four sources
5. Merges with outer joins so Zillow rows survive when Census names don't match perfectly
6. Filters to the top 100 metros by Zillow's SizeRank to reduce noise from small-sample markets
7. Computes composite scores and writes the result to `data/metro_markets_balanced.csv`

### Scoring

Each metro is scored across three composites, each a weighted sum of normalized signals:

- **Growth score** — weights rent growth, home value growth, and population growth most heavily
- **Value score** — weights gross yield (monthly rent × 12 / home value) and rent growth most heavily
- **Balanced score** — blends all four signals (rent, home value, population, income growth) evenly

Each thesis in the dashboard applies its own pre-filter and sorts by a different composite. For example, the Yield thesis keeps only metros with gross yield > 7% and sorts by yield descending; the Growth thesis keeps only metros with population growth > 2% and rent growth > 3%, then sorts by growth score.

### Dashboard (`app.py`)

Reads the CSV with Streamlit caching and renders:

- A ranked data table with market search
- A horizontal bar chart of composite scores for the top N
- A US state-level choropleth heatmap showing average score per state
- A CSV download of the filtered results

Controls in the sidebar let the user switch thesis, adjust top-N, and search by market name.

### Robustness

The Census ACS API has been flaky during development, so the pipeline retries with exponential backoff on transient failures and falls back to 3-signal scoring (without income) when the API is permanently unreachable. The dashboard detects whether income data is present in the loaded CSV and hides or shows the income column accordingly.

## Run it locally

Prerequisites: Python 3.10+ and pip.

```bash
git clone https://github.com/lgisonda/real-estate-ai.git
cd real-estate-ai
pip install -r requirements.txt
python backend/main.py          # generate data/metro_markets_balanced.csv
streamlit run app.py            # open http://localhost:8501
```

## Notes

- Scoring weights are illustrative — this is not financial advice.
- Metro-level averages smooth over significant intra-market variation; a high score doesn't mean every ZIP code in that metro is a good investment.
- Data refresh is currently manual. Rerun `python backend/main.py`, commit the new CSV, push to trigger a Streamlit Cloud redeploy.