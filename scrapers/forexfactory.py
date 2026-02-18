"""
Economic calendar scraper — ForexFactory JSON + Myfxbook fallback.

Primary source: ForexFactory CDN JSON feeds (thisweek / nextweek)
  https://nfs.faireconomy.media/ff_calendar_{period}.json

Fallback / extended range: Myfxbook Economic Calendar API
  https://www.myfxbook.com/services/forex-economic-calendar-api/getEconomicCalendar.json

No API key required for either source.
"""

import hashlib
import logging
import re
from datetime import datetime, timezone, timedelta

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# ForexFactory CDN (weekly feeds)
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

# ---------------------------------------------------------------------------
# Myfxbook Economic Calendar API (date-range, covers months)
# ---------------------------------------------------------------------------

MYFXBOOK_URL = (
    "https://www.myfxbook.com/services/forex-economic-calendar-api/"
    "getEconomicCalendar.json"
)

MYFXBOOK_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/121.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Referer": "https://www.myfxbook.com/forex-economic-calendar",
}

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

TRACKED_CURRENCIES = {"USD", "EUR", "CNY"}

FF_IMPACT_MAP = {
    "High":         "High",
    "Medium":       "Medium",
    "Low":          "Low",
    "Holiday":      "Low",
    "Non-Economic": "Low",
}

# Myfxbook returns numeric strings: "3"=High, "2"=Medium, "1"=Low, "0"=Holiday
MYFXBOOK_IMPACT_MAP = {
    "3": "High",
    "2": "Medium",
    "1": "Low",
    "0": "Low",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _event_id(country: str, title: str, date_str: str) -> str:
    """Stable deduplication key for an event."""
    raw = f"{country}|{title}|{date_str}"
    return hashlib.md5(raw.encode()).hexdigest()


def _parse_dt_iso(date_str: str) -> datetime | None:
    """Parse ISO-8601 date string (ForexFactory format) to UTC datetime."""
    if not date_str:
        return None
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%S%z")
        return dt.astimezone(timezone.utc).replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    # Fallback: strip offset, assume UTC
    m = re.match(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})", date_str)
    if m:
        try:
            return datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            pass
    return None


def _parse_dt_myfxbook(date_str: str) -> datetime | None:
    """Parse Myfxbook date string 'YYYY-MM-DD HH:MM' (UTC) to UTC datetime."""
    if not date_str:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(date_str, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def _clean(val) -> str | None:
    v = (val or "").strip()
    return v if v else None


# ---------------------------------------------------------------------------
# ForexFactory JSON fetcher
# ---------------------------------------------------------------------------

def _fetch_forexfactory(period: str) -> list[dict]:
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

        impact = FF_IMPACT_MAP.get(item.get("impact", "Low"), "Low")
        title = _clean(item.get("title"))
        if not title:
            continue

        event_dt = _parse_dt_iso(item.get("date", ""))
        if event_dt is None:
            continue

        date_str = event_dt.isoformat()
        events.append({
            "event_id":   _event_id(currency, title, date_str),
            "title":      title,
            "country":    currency,
            "event_date": event_dt,
            "impact":     impact,
            "forecast":   _clean(item.get("forecast")),
            "previous":   _clean(item.get("previous")),
            "actual":     _clean(item.get("actual")),
        })

    logger.info("ForexFactory period=%s: parsed %d events", period, len(events))
    return events


# ---------------------------------------------------------------------------
# Myfxbook JSON fetcher (date-range)
# ---------------------------------------------------------------------------

def _fetch_myfxbook(start_dt: datetime, end_dt: datetime) -> list[dict]:
    params = {
        "start": start_dt.strftime("%Y-%m-%d %H:%M"),
        "end":   end_dt.strftime("%Y-%m-%d %H:%M"),
    }
    try:
        resp = requests.get(
            MYFXBOOK_URL, headers=MYFXBOOK_HEADERS, params=params, timeout=25
        )
        resp.raise_for_status()
        raw_events = resp.json()
    except Exception as exc:
        logger.error("Myfxbook fetch failed (%s – %s): %s", params["start"], params["end"], exc)
        return []

    events: list[dict] = []
    for item in raw_events:
        currency = item.get("currency", "") or item.get("country", "")
        if currency not in TRACKED_CURRENCIES:
            continue

        impact_raw = str(item.get("impact", "1"))
        impact = MYFXBOOK_IMPACT_MAP.get(impact_raw, "Low")

        title = _clean(item.get("name") or item.get("title"))
        if not title:
            continue

        date_raw = item.get("date", "")
        event_dt = _parse_dt_myfxbook(date_raw) or _parse_dt_iso(date_raw)
        if event_dt is None:
            continue

        date_str = event_dt.isoformat()
        events.append({
            "event_id":   _event_id(currency, title, date_str),
            "title":      title,
            "country":    currency,
            "event_date": event_dt,
            "impact":     impact,
            "forecast":   _clean(item.get("forecast")),
            "previous":   _clean(item.get("previous")),
            "actual":     _clean(item.get("actual")),
        })

    logger.info("Myfxbook: parsed %d events (%s – %s)", len(events), params["start"], params["end"])
    return events


# ---------------------------------------------------------------------------
# Public sync entry-point (called by app.py)
# ---------------------------------------------------------------------------

def sync(db, EconomicEvent) -> tuple[int, int]:
    """
    Fetch events for this week + next week (ForexFactory JSON),
    then extend with Myfxbook for broader date coverage.
    Returns (new_count, updated_count).
    """
    all_events: list[dict] = []
    seen_ids: set[str] = set()

    # ── ForexFactory weekly feeds ─────────────────────────────────────────
    ff_ok = False
    for period in ("thisweek", "nextweek"):
        for ev in _fetch_forexfactory(period):
            if ev["event_id"] not in seen_ids:
                seen_ids.add(ev["event_id"])
                all_events.append(ev)
                ff_ok = True

    # ── Myfxbook date-range (covers this month + next month) ─────────────
    now = datetime.now(timezone.utc)
    # Start of current month, end of next month
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if now.month == 12:
        end = now.replace(year=now.year + 1, month=1, day=31, hour=23, minute=59)
    else:
        import calendar
        last_day = calendar.monthrange(now.year, now.month + 1)[1]
        end = now.replace(month=now.month + 1, day=last_day, hour=23, minute=59)

    for ev in _fetch_myfxbook(start, end):
        if ev["event_id"] not in seen_ids:
            seen_ids.add(ev["event_id"])
            all_events.append(ev)

    if not all_events:
        logger.warning("No events fetched from any source")

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
        "Calendar sync complete: +%d new, %d updated", new_count, updated_count
    )
    return new_count, updated_count
