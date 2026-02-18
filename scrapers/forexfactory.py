"""
Economic calendar scraper — ForexFactory JSON + FXStreet fallback.

Primary:  ForexFactory CDN (thisweek only — the only valid period on the CDN)
          https://nfs.faireconomy.media/ff_calendar_thisweek.json
          Field note: currency is in the "country" key, not "currency".

Extended: FXStreet Economic Calendar API (date-range, no key required)
          https://calendar.fxstreet.com/eventdate/

Both sources fail gracefully. Events are deduplicated before upsert.
"""

import hashlib
import logging
import re
import calendar
from datetime import datetime, timezone, timedelta

import requests

logger = logging.getLogger(__name__)

TRACKED_CURRENCIES = {"USD", "EUR", "CNY"}

# ---------------------------------------------------------------------------
# ForexFactory CDN  — weekly JSON feed
# ---------------------------------------------------------------------------

FF_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"

FF_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/121.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, */*",
}

FF_IMPACT_MAP = {
    "High":         "High",
    "Medium":       "Medium",
    "Low":          "Low",
    "Holiday":      "Low",
    "Non-Economic": "Low",
}

# ---------------------------------------------------------------------------
# FXStreet Economic Calendar API  — date-range JSON
# ---------------------------------------------------------------------------

FXSTREET_URL = "https://calendar.fxstreet.com/eventdate/"

FXSTREET_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/121.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.fxstreet.com/economic-calendar",
}

# FXStreet volatility value → our impact label
FXSTREET_IMPACT_MAP = {
    "High":   "High",
    "Medium": "Medium",
    "Low":    "Low",
    "None":   "Low",
    "0":      "Low",
    "1":      "Low",
    "2":      "Medium",
    "3":      "High",
}

# FXStreet currency-code field name variants seen in the wild
_FXS_CURRENCY_KEYS = ("CurrencyCode", "Currency", "currency", "country")
# FXStreet event-name field name variants
_FXS_NAME_KEYS = ("Name", "EventName", "name", "title", "Title")
# FXStreet date field name variants (UTC)
_FXS_DATE_KEYS = ("DateUtc", "Date", "date", "EventDate")
# FXStreet volatility/impact field name variants
_FXS_IMPACT_KEYS = ("VolatilityString", "Volatility", "volatility", "impact")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _event_id(country: str, title: str, date_str: str) -> str:
    raw = f"{country}|{title}|{date_str}"
    return hashlib.md5(raw.encode()).hexdigest()


def _parse_dt(date_str: str) -> datetime | None:
    """Parse ISO-8601 or 'YYYY-MM-DD HH:MM' date string → UTC datetime."""
    if not date_str:
        return None
    # ISO 8601 with offset, e.g. "2026-02-18T13:30:00-0500" or "…Z"
    cleaned = date_str.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(cleaned)
        return dt.astimezone(timezone.utc).replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    # Space-separated UTC, e.g. "2026-02-18 13:30"
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(date_str, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def _clean(val) -> str | None:
    v = (val or "").strip()
    return v if v else None


def _first_value(d: dict, keys) -> str:
    for k in keys:
        v = d.get(k)
        if v is not None:
            return str(v)
    return ""


# ---------------------------------------------------------------------------
# ForexFactory fetcher
# ---------------------------------------------------------------------------

def _fetch_forexfactory() -> list[dict]:
    try:
        resp = requests.get(FF_URL, headers=FF_HEADERS, timeout=25)
        resp.raise_for_status()
        raw = resp.json()
    except Exception as exc:
        logger.error("ForexFactory fetch failed: %s", exc)
        return []

    if not isinstance(raw, list):
        logger.error("ForexFactory: unexpected response type %s", type(raw))
        return []

    if raw:
        logger.debug("ForexFactory sample keys: %s", list(raw[0].keys()))

    events: list[dict] = []
    for item in raw:
        # FF CDN uses "country" for the currency code (not "currency")
        currency = item.get("country") or item.get("currency") or ""
        if currency not in TRACKED_CURRENCIES:
            continue

        impact = FF_IMPACT_MAP.get(item.get("impact", "Low"), "Low")
        title = _clean(item.get("title"))
        if not title:
            continue

        event_dt = _parse_dt(item.get("date", ""))
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

    logger.info("ForexFactory thisweek: parsed %d events", len(events))
    return events


# ---------------------------------------------------------------------------
# FXStreet fetcher
# ---------------------------------------------------------------------------

def _fetch_fxstreet(start_dt: datetime, end_dt: datetime) -> list[dict]:
    params = {
        "culture":  "en-US",
        "dateFrom": start_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "dateTo":   end_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "volatility": "0,1,2,3",
    }
    try:
        resp = requests.get(FXSTREET_URL, headers=FXSTREET_HEADERS, params=params, timeout=30)
        resp.raise_for_status()
        raw = resp.json()
    except Exception as exc:
        logger.error("FXStreet fetch failed: %s", exc)
        return []

    if not isinstance(raw, list):
        logger.error("FXStreet: unexpected response type %s", type(raw))
        return []

    if raw:
        logger.debug("FXStreet sample keys: %s", list(raw[0].keys()))

    events: list[dict] = []
    for item in raw:
        currency = _first_value(item, _FXS_CURRENCY_KEYS)
        if currency not in TRACKED_CURRENCIES:
            continue

        impact_raw = _first_value(item, _FXS_IMPACT_KEYS)
        impact = FXSTREET_IMPACT_MAP.get(impact_raw, "Low")

        title = _clean(_first_value(item, _FXS_NAME_KEYS))
        if not title:
            continue

        event_dt = _parse_dt(_first_value(item, _FXS_DATE_KEYS))
        if event_dt is None:
            continue

        date_str = event_dt.isoformat()
        events.append({
            "event_id":   _event_id(currency, title, date_str),
            "title":      title,
            "country":    currency,
            "event_date": event_dt,
            "impact":     impact,
            "forecast":   _clean(item.get("Forecast") or item.get("forecast")),
            "previous":   _clean(item.get("Previous") or item.get("previous")),
            "actual":     _clean(item.get("Actual") or item.get("actual")),
        })

    logger.info("FXStreet: parsed %d events", len(events))
    return events


# ---------------------------------------------------------------------------
# Public sync entry-point
# ---------------------------------------------------------------------------

def sync(db, EconomicEvent) -> tuple[int, int]:
    """
    Fetch events from ForexFactory (this week) + FXStreet (this+next month).
    Returns (new_count, updated_count).
    """
    all_events: list[dict] = []
    seen_ids: set[str] = set()

    # ── ForexFactory: current week ────────────────────────────────────────
    for ev in _fetch_forexfactory():
        if ev["event_id"] not in seen_ids:
            seen_ids.add(ev["event_id"])
            all_events.append(ev)

    # ── FXStreet: this month + next month ─────────────────────────────────
    now = datetime.now(timezone.utc)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    next_month = now.month % 12 + 1
    next_year  = now.year + (1 if now.month == 12 else 0)
    last_day   = calendar.monthrange(next_year, next_month)[1]
    end = now.replace(year=next_year, month=next_month, day=last_day,
                      hour=23, minute=59, second=59, microsecond=0)

    for ev in _fetch_fxstreet(start, end):
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
