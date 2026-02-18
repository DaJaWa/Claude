"""
S&P 500 market data fetcher using yfinance (free, no API key required).

Pulls daily OHLCV for ^GSPC and computes the daily % change so we can
later correlate economic surprises with market moves.
"""

import logging
from datetime import date, timedelta

import yfinance as yf

logger = logging.getLogger(__name__)

SP500 = "^GSPC"


def fetch_history(days: int = 365) -> "pandas.DataFrame | None":
    """Download up to `days` of daily S&P 500 history. Returns None on error."""
    try:
        ticker = yf.Ticker(SP500)
        hist = ticker.history(period=f"{days}d", auto_adjust=True)
        if hist.empty:
            logger.error("yfinance returned empty history for %s", SP500)
            return None
        return hist
    except Exception as exc:
        logger.error("Failed to fetch market data: %s", exc)
        return None


def sync(db, MarketData, days: int = 365) -> int:
    """
    Fetch S&P 500 history and upsert into the DB.

    Computes pct_change relative to the previous trading day's close.
    Returns the number of new rows inserted.
    """
    hist = fetch_history(days=days)
    if hist is None:
        return 0

    # Build an ordered list of (date, close) to compute pct_change
    rows = []
    for idx, row in hist.iterrows():
        trade_date = idx.date() if hasattr(idx, "date") else idx
        rows.append(
            {
                "date": trade_date,
                "open": float(row["Open"]),
                "high": float(row["High"]),
                "low": float(row["Low"]),
                "close": float(row["Close"]),
                "volume": int(row.get("Volume", 0)),
            }
        )

    # Compute pct_change inline
    for i, r in enumerate(rows):
        if i == 0:
            r["pct_change"] = None
        else:
            prev_close = rows[i - 1]["close"]
            r["pct_change"] = (
                ((r["close"] - prev_close) / prev_close) * 100
                if prev_close and prev_close > 0
                else None
            )

    new_count = 0
    for r in rows:
        existing = MarketData.query.filter_by(date=r["date"]).first()
        if existing:
            # Always refresh pct_change in case prev row just arrived
            existing.pct_change = r["pct_change"]
        else:
            db.session.add(MarketData(**r))
            new_count += 1

    db.session.commit()
    logger.info("Market data sync complete: %d new rows", new_count)
    return new_count
