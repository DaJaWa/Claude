# Economic Calendar Tracker

A Flask web dashboard that tracks economic and financial release calendars for the **US**, **EU**, and **China**, with historical surprise-vs-S&P 500 analysis.

## Features

| Page | What it does |
|------|-------------|
| **Dashboard** | Upcoming high-impact events (next 30 days), S&P 500 chart with event markers, today's events summary |
| **Calendar** | FullCalendar month/week/list view, filterable by country and impact level |
| **Analysis** | Scatter plot of indicator surprises (actual − forecast) vs. S&P 500 daily return, Pearson correlation |

## Data Sources (all free, no API key required)

| Source | Data |
|--------|------|
| [ForexFactory JSON feed](https://nfs.faireconomy.media) | Economic calendar — US (USD), EU (EUR), China (CNY) events with impact, forecast, actual |
| [yfinance](https://github.com/ranaroussi/yfinance) | S&P 500 (`^GSPC`) daily OHLCV history |

Data refreshes automatically every 4 hours, or manually via the **Refresh Data** button.

## Quick Start

```bash
# 1. Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Run the app
python app.py
```

Then open [http://localhost:5000](http://localhost:5000).

## Optional Configuration

Create a `.env` file in the project root:

```env
# Flask secret key (change for production)
SECRET_KEY=your-secret-here

# Path to SQLite DB (default: instance/economic_calendar.db)
DATABASE_URL=sqlite:///economic_calendar.db
```

## Tracked Indicators

**US (USD) — High impact:** Non-Farm Payrolls, CPI, FOMC Rate Decision, GDP, PCE, Retail Sales, ISM PMIs, PPI, Consumer Confidence

**EU (EUR) — High impact:** ECB Rate Decision, Flash GDP, Flash CPI, Unemployment, PMI Composite, German IFO, ZEW Sentiment

**China (CNY) — High impact:** GDP, Manufacturing PMI, Caixin PMI, CPI, Trade Balance, Industrial Production, Retail Sales

## Notes

- **Historical analysis builds over time.** The ForexFactory feed includes actual values for past events in the current and recent months. The more the app runs, the richer the analysis becomes.
- Times are stored in UTC. The ForexFactory feed provides Eastern Time (ET); the scraper converts automatically.
- S&P 500 data covers the last 365 days of trading.
