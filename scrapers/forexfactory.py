"""
ForexFactory economic calendar scraper.

Uses the unofficial JSON feed at nfs.faireconomy.media which mirrors
ForexFactory calendar data as structured JSON. No API key required.

All timestamps in the feed are Eastern Time (ET); we convert to UTC before
storing in the database.
"""

import hashlib
import logging
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")

IMPACT_MAP = {
    "High": "High",
    "Medium": "Medium",
    "Low": "Low",
    "Holiday": None,        # skip holidays
    "Non-Economic": None,   # skip
}

TRACKED_CURRENCIES = {"USD", "EUR", "CNY"}

FF_ENDPOINTS = {
    "this_week":  "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
    "next_week":  "https://nfs.faireconomy.media/ff_calendar_nextweek.json",
    "this_month": "https://nfs.faireconomy.media/ff_calendar_thismonth.json",
    "next_month": "https://nfs.faireconomy.media/ff_calendar_nextmonth.json",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; EconomicCalendarTracker/1.0; "
        "+https://github.com/economic-tracker)"
    ),
    "Accept": "application/json",
}


def _parse_date(date_str: str) -> datetime | None:
    """
    Parse a ForexFactory date string into a UTC-aware datetime.

    Expected formats:
      - "Feb 07 2025 8:30am"   (datetime with time, 12-hour, ET)
      - "Feb 07 2025"          (all-day event, assumed 00:00 ET)
    """
    if not date_str:
        return None

    date_str = date_str.strip()

    # Normalise am/pm to uppercase so strptime works cross-platform
    date_str = re.sub(r"(am|pm)$", lambda m: m.group().upper(), date_str, flags=re.IGNORECASE)

    for fmt in ("%b %d %Y %I:%M%p", "%b %d %Y %I%p", "%b %d %Y"):
        try:
            naive = datetime.strptime(date_str, fmt)
            return naive.replace(tzinfo=ET).astimezone(ZoneInfo("UTC"))
        except ValueError:
            continue

    logger.warning("Could not parse date string: %r", date_str)
    return None


def _event_id(country: str, title: str, date_str: str) -> str:
    """Stable deduplication key for an event."""
    raw = f"{country}|{title}|{date_str}"
    return hashlib.md5(raw.encode()).hexdigest()


def fetch_raw(period: str = "this_week") -> list[dict]:
    """Fetch raw JSON for a calendar period. Returns [] on failure."""
    url = FF_ENDPOINTS.get(period)
    if not url:
        logger.error("Unknown period: %s", period)
        return []
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.error("Failed to fetch %s (%s): %s", period, url, exc)
        return []


def parse_events(raw: list[dict]) -> list[dict]:
    """Filter and normalise raw ForexFactory JSON into our schema."""
    out = []
    for item in raw:
        country = (item.get("country") or "").upper()
        if country not in TRACKED_CURRENCIES:
            continue

        raw_impact = item.get("impact", "Low")
        impact = IMPACT_MAP.get(raw_impact)
        if impact is None:
            continue  # holiday / non-economic

        date_str = item.get("date", "")
        event_date = _parse_date(date_str)
        if not event_date:
            continue

        title = (item.get("title") or "").strip()
        if not title:
            continue

        out.append(
            {
                "event_id": _event_id(country, title, date_str),
                "title": title,
                "country": country,
                "event_date": event_date,
                "impact": impact,
                "forecast": (item.get("forecast") or "").strip() or None,
                "previous": (item.get("previous") or "").strip() or None,
                "actual": (item.get("actual") or "").strip() or None,
            }
        )
    return out


def sync(db, EconomicEvent) -> tuple[int, int]:
    """
    Fetch events for this month + next month, upsert into the DB.

    Returns (new_count, updated_count).
    """
    raw: list[dict] = []
    for period in ("this_month", "next_month"):
        raw.extend(fetch_raw(period))

    events = parse_events(raw)

    new_count = 0
    updated_count = 0

    for ev in events:
        existing = EconomicEvent.query.filter_by(event_id=ev["event_id"]).first()
        if existing:
            # Only update forecast/previous/actual if we have new info
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
    logger.info("ForexFactory sync complete: +%d new, %d updated", new_count, updated_count)
    return new_count, updated_count
