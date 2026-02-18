"""
Economic calendar scraper — ForexFactory JSON backend.

ForexFactory publishes its calendar as a JSON feed hosted at
  https://nfs.faireconomy.media/ff_calendar_<period>.json

Periods available:
  this_week, next_week, this_month, next_month

No API key required.  Events are returned with pre-parsed fields
(date, time, currency, impact, title, forecast, previous, actual).
"""

import hashlib
import logging
import re
from datetime import datetime, timezone

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# ForexFactory / FairEconomy CDN endpoint
# ---------------------------------------------------------------------------

FF_BASE_URL = "https://nfs.faireconomy.media/ff_calendar_{period}.json"

FF_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/121.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}

TRACKED_CURRENCIES = {"USD", "EUR", "CNY"}

# ForexFactory impact strings → our canonical labels
IMPACT_MAP = {
    "High":    "High",
    "Medium":  "Medium",
    "Low":     "Low",
    "Holiday": "Low",   # treat exchange holidays as Low-impact
    "Non-Economic": "Low",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _event_id(country: str, title: str, date_str: str) -> str:
    """Stable deduplication key for an event."""
    raw = f"{country}|{title}|{date_str}"
    return hashlib.md5(raw.encode()).hexdigest()


def _parse_dt(date_str: str) -> datetime | None:
    """
    Parse a ForexFactory date string to an aware UTC datetime.

    ForexFactory uses ISO-8601 with UTC offset, e.g.:
      "2026-02-18T08:30:00-0500"
    Times marked "Tentative" or "All Day" arrive as midnight of the date.
    """
    if not date_str:
        return None
    # Try ISO 8601 with offset  (most events)
    for fmt in (
        "%Y-%m-%dT%H:%M:%S%z",   # handles ±HH:MM and ±HHMM
    ):
        try:
            dt = datetime.strptime(date_str, fmt)
            return dt.astimezone(timezone.utc).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    # Fallback: strip offset and assume UTC
    m = re.match(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})", date_str)
    if m:
        try:
            return datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            pass
    logger.debug("Could not parse date string: %r", date_str)
    return None


# ---------------------------------------------------------------------------
# Fetch + parse
# ---------------------------------------------------------------------------

def fetch_events(period: str) -> list[dict]:
    """
    Fetch the ForexFactory JSON feed for *period* and return our event dicts.

    period must be one of: this_week, next_week, this_month, next_month
    """
    url = FF_BASE_URL.format(period=period)
    try:
        resp = requests.get(url, headers=FF_HEADERS, timeout=25)
        resp.raise_for_status()
        raw_events = resp.json()
    except Exception as exc:
        logger.error("ForexFactory fetch failed (period=%s): %s", period, exc)
        return []

    events: list[dict] = []
    for item in raw_events:
        currency = item.get("currency", "")
        if currency not in TRACKED_CURRENCIES:
            continue

        impact_raw = item.get("impact", "Low")
        impact = IMPACT_MAP.get(impact_raw, "Low")

        title = (item.get("title") or "").strip()
        if not title:
            continue

        event_dt = _parse_dt(item.get("date", ""))
        if event_dt is None:
            continue

        date_str = event_dt.isoformat()

        def _clean(val):
            v = (val or "").strip()
            return v if v else None

        events.append(
            {
                "event_id":   _event_id(currency, title, date_str),
                "title":      title,
                "country":    currency,
                "event_date": event_dt,
                "impact":     impact,
                "forecast":   _clean(item.get("forecast")),
                "previous":   _clean(item.get("previous")),
                "actual":     _clean(item.get("actual")),
            }
        )

    logger.info("ForexFactory period=%s: parsed %d events", period, len(events))
    return events


# ---------------------------------------------------------------------------
# Public sync entry-point (called by app.py)
# ---------------------------------------------------------------------------

def sync(db, EconomicEvent) -> tuple[int, int]:
    """
    Fetch events for this month + next month, upsert into the DB.
    Returns (new_count, updated_count).
    """
    all_events: list[dict] = []
    seen_ids: set[str] = set()

    for period in ("thismonth", "nextmonth"):
        for ev in fetch_events(period):
            if ev["event_id"] not in seen_ids:
                seen_ids.add(ev["event_id"])
                all_events.append(ev)

    new_count = 0
    updated_count = 0

    for ev in all_events:
        existing = EconomicEvent.query.filter_by(event_id=ev["event_id"]).first()
        if existing:
            changed = False
            for field in ("forecast", "previous", "actual"):
                if ev[field] and not getattr(existing, field):
                    setattr(existing, field, ev[field])
                    changed = True
            if changed:
                updated_count += 1
        else:
            db.session.add(EconomicEvent(**ev))
            new_count += 1

    db.session.commit()
    logger.info(
        "ForexFactory sync complete: +%d new, %d updated", new_count, updated_count
    )
    return new_count, updated_count
