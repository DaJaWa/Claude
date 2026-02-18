"""
Economic calendar scraper — Investing.com backend.

Fetches the economic calendar from Investing.com's internal AJAX endpoint.
No API key required.  The response is a JSON envelope whose ``data`` field
contains an HTML fragment; we parse that with BeautifulSoup.

Timezone note: we request timeZone=55 (UTC) so all times are stored as UTC.
"""

import hashlib
import logging
import re
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Investing.com AJAX endpoint
# ---------------------------------------------------------------------------

IC_URL = (
    "https://www.investing.com/economic-calendar/"
    "Service/getCalendarFilteredData"
)

IC_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/121.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Content-Type": "application/x-www-form-urlencoded",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://www.investing.com/economic-calendar/",
    "Origin": "https://www.investing.com",
}

# Investing.com country IDs we care about
COUNTRY_IDS = {
    "5":  "USD",   # United States
    "72": "EUR",   # Euro Zone
    "37": "CNY",   # China
}

# Span title → our currency code (fallback lookup from page text)
TITLE_TO_CURRENCY = {
    "United States": "USD",
    "Euro Zone":     "EUR",
    "China":         "CNY",
}

TRACKED_CURRENCIES = {"USD", "EUR", "CNY"}

# data-img_key on the sentiment <td>
IMPACT_MAP = {
    "bull3": "High",
    "bull2": "Medium",
    "bull1": "Low",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _event_id(country: str, title: str, date_str: str) -> str:
    """Stable deduplication key for an event."""
    raw = f"{country}|{title}|{date_str}"
    return hashlib.md5(raw.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Fetch + parse
# ---------------------------------------------------------------------------

def fetch_raw_html(tab: str = "this_week") -> str:
    """
    POST to Investing.com AJAX endpoint for one calendar tab.
    Returns the HTML fragment string, or '' on failure.
    """
    payload = {
        "country[]": list(COUNTRY_IDS.keys()),
        "importance[]": ["3", "2", "1"],
        "timeZone": "55",          # UTC
        "timeFilter": "timeOnly",
        "currentTab": tab,
        "submitFilters": "1",
        "limit_from": "0",
    }
    try:
        resp = requests.post(IC_URL, headers=IC_HEADERS, data=payload, timeout=25)
        resp.raise_for_status()
        json_resp = resp.json()
        return json_resp.get("data", "")
    except Exception as exc:
        logger.error("Investing.com fetch failed (tab=%s): %s", tab, exc)
        return ""


def parse_events_from_html(html: str) -> list[dict]:
    """
    Parse the Investing.com calendar HTML fragment into our event schema.

    The fragment contains a mix of:
      - ``<tr class="theDay">`` rows  — carry the current date
      - ``<tr class="js-event-item">`` rows — one per economic event
    """
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    events: list[dict] = []
    current_date = None

    for row in soup.find_all("tr"):
        classes = row.get("class", [])

        # ── date-header row ──────────────────────────────────────────────
        if "theDay" in classes:
            span = row.find("span", class_="fleft")
            if span:
                raw = span.get_text(strip=True)
                for fmt in ("%A, %B %d, %Y", "%A, %b %d, %Y"):
                    try:
                        current_date = datetime.strptime(raw, fmt).date()
                        break
                    except ValueError:
                        pass
            continue

        # ── event row ────────────────────────────────────────────────────
        if "js-event-item" not in classes:
            continue
        if current_date is None:
            continue

        # --- currency ---------------------------------------------------
        flag_td = row.find("td", class_="flagCur")
        currency = None
        if flag_td:
            span = flag_td.find("span", title=True)
            if span:
                currency = TITLE_TO_CURRENCY.get(span["title"])
            if not currency:
                # fall back to the text "USD" / "EUR" / "CNY" in the cell
                txt = flag_td.get_text(strip=True)
                if txt in TRACKED_CURRENCIES:
                    currency = txt
        if currency not in TRACKED_CURRENCIES:
            continue

        # --- impact -----------------------------------------------------
        sent_td = row.find("td", class_="sentiment")
        impact = "Low"
        if sent_td:
            img_key = sent_td.get("data-img_key", "")
            impact = IMPACT_MAP.get(img_key, "Low")

        # --- event title ------------------------------------------------
        event_td = row.find("td", class_="event")
        title = ""
        if event_td:
            a = event_td.find("a")
            title = (a or event_td).get_text(strip=True)
        if not title:
            continue

        # --- time -------------------------------------------------------
        time_td = row.find("td", class_="js-time") or row.find("td", class_="time")
        h, m = 0, 0
        if time_td:
            t = time_td.get_text(strip=True)
            match = re.match(r"^(\d{1,2}):(\d{2})$", t)
            if match:
                h, m = int(match.group(1)), int(match.group(2))

        event_dt = datetime(
            current_date.year, current_date.month, current_date.day,
            h, m, tzinfo=timezone.utc,
        )

        # --- actual / forecast / previous --------------------------------
        def _cell(css_class: str) -> str | None:
            td = row.find("td", class_=css_class)
            if td:
                val = td.get_text(strip=True)
                return val if val else None
            return None

        actual   = _cell("act")
        forecast = _cell("fore")
        previous = _cell("prev")

        date_str = event_dt.isoformat()
        events.append(
            {
                "event_id":   _event_id(currency, title, date_str),
                "title":      title,
                "country":    currency,
                "event_date": event_dt,
                "impact":     impact,
                "forecast":   forecast,
                "previous":   previous,
                "actual":     actual,
            }
        )

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

    for tab in ("this_month", "next_month"):
        html = fetch_raw_html(tab)
        parsed = parse_events_from_html(html)
        logger.info("Tab %s: parsed %d events from HTML", tab, len(parsed))
        for ev in parsed:
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
        "Investing.com sync complete: +%d new, %d updated", new_count, updated_count
    )
    return new_count, updated_count
