import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key-change-in-production")
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", "sqlite:///economic_calendar.db"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # How long before data is considered stale (seconds)
    CACHE_TIMEOUT = 4 * 60 * 60  # 4 hours

    # Currencies / regions tracked
    TRACKED_CURRENCIES = ["USD", "EUR", "CNY"]
    COUNTRY_MAP = {
        "USD": {"name": "United States", "flag": "🇺🇸", "region": "US"},
        "EUR": {"name": "European Union", "flag": "🇪🇺", "region": "EU"},
        "CNY": {"name": "China", "flag": "🇨🇳", "region": "CN"},
    }

    # ForexFactory unofficial JSON endpoints (Eastern Time data)
    FF_BASE = "https://nfs.faireconomy.media"
    FF_ENDPOINTS = {
        "this_week": "/ff_calendar_thisweek.json",
        "next_week": "/ff_calendar_nextweek.json",
        "this_month": "/ff_calendar_thismonth.json",
        "next_month": "/ff_calendar_nextmonth.json",
    }

    # Optional: FRED API key for richer historical US data
    FRED_API_KEY = os.environ.get("FRED_API_KEY", "")

    # S&P 500 ticker
    SP500_TICKER = "^GSPC"

    # Key high-impact indicators to highlight
    KEY_INDICATORS = {
        "USD": [
            "Non-Farm Employment Change",
            "CPI m/m",
            "Core CPI m/m",
            "Federal Funds Rate",
            "GDP q/q",
            "PCE Price Index m/m",
            "Retail Sales m/m",
            "ISM Manufacturing PMI",
            "ISM Services PMI",
            "Unemployment Rate",
            "PPI m/m",
            "Consumer Confidence",
        ],
        "EUR": [
            "Main Refinancing Rate",
            "Flash GDP q/q",
            "Flash CPI y/y",
            "Unemployment Rate",
            "PMI Composite",
            "German IFO Business Climate",
            "ZEW Economic Sentiment",
        ],
        "CNY": [
            "GDP y/y",
            "Manufacturing PMI",
            "Caixin Manufacturing PMI",
            "CPI y/y",
            "Trade Balance",
            "Industrial Production y/y",
            "Retail Sales y/y",
        ],
    }
