"""
Economic & Financial Release Calendar — Flask application.

Routes:
  GET  /                        Dashboard: upcoming high-impact events + S&P chart
  GET  /calendar                FullCalendar view with country / impact filters
  GET  /analysis                Historical surprise-vs-market scatter analysis
  GET  /api/events              JSON events for FullCalendar
  GET  /api/market              JSON S&P 500 time-series
  GET  /api/analysis/<title>    JSON historical data for one indicator
  POST /api/refresh             Manually trigger a data refresh
"""

import json
import logging
import re
from datetime import datetime, timezone, timedelta, date

from flask import Flask, jsonify, redirect, render_template, request, url_for

from config import Config
from models import DataSync, EconomicEvent, MarketData, db
from scrapers import forexfactory, market as market_scraper

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config.from_object(Config)
db.init_app(app)

with app.app_context():
    db.create_all()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

IMPACT_COLORS = {
    "High":   "#ef4444",
    "Medium": "#f97316",
    "Low":    "#eab308",
}

COUNTRY_COLORS = {
    "USD": "#1d4ed8",
    "EUR": "#16a34a",
    "CNY": "#dc2626",
}


def _last_sync_time() -> datetime | None:
    sync = DataSync.query.filter_by(source="forexfactory").first()
    if sync and sync.last_synced:
        return sync.last_synced.replace(tzinfo=timezone.utc)
    return None


def _is_stale() -> bool:
    last = _last_sync_time()
    if not last:
        return True
    age = (datetime.now(timezone.utc) - last).total_seconds()
    return age > Config.CACHE_TIMEOUT


def _do_refresh() -> dict:
    new_ev, upd_ev = forexfactory.sync(db, EconomicEvent)
    new_mkt = market_scraper.sync(db, MarketData)

    sync = DataSync.query.filter_by(source="forexfactory").first()
    if not sync:
        sync = DataSync(source="forexfactory")
        db.session.add(sync)
    sync.last_synced = datetime.now(timezone.utc)
    sync.status = "success"
    sync.message = (
        f"Events: +{new_ev} new, {upd_ev} updated | Market: +{new_mkt} new rows"
    )
    db.session.commit()
    return {"events_new": new_ev, "events_updated": upd_ev, "market_new": new_mkt}


def _parse_numeric(val: str) -> float | None:
    """Strip non-numeric characters and parse to float. Returns None on failure."""
    if not val:
        return None
    cleaned = re.sub(r"[^\d.\-]", "", val)
    try:
        return float(cleaned)
    except ValueError:
        return None


def _compute_surprise(actual: str, forecast: str) -> float | None:
    """
    Normalised surprise as a percentage deviation from forecast.
    Returns None when values can't be parsed.
    """
    a = _parse_numeric(actual)
    f = _parse_numeric(forecast)
    if a is None or f is None:
        return None
    if f == 0:
        return a - f
    return ((a - f) / abs(f)) * 100


# ---------------------------------------------------------------------------
# Page routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    if _is_stale():
        try:
            _do_refresh()
        except Exception as exc:
            logger.error("Auto-refresh failed: %s", exc)

    today = datetime.now(timezone.utc).date()

    upcoming_high = EconomicEvent.get_upcoming(days=30, impacts=["High"])
    today_events = [e for e in upcoming_high if e.event_date.date() == today]
    week_events = [
        e for e in upcoming_high
        if today <= e.event_date.date() <= today + timedelta(days=7)
    ]

    market_rows = MarketData.get_recent(days=90)
    last_sync = _last_sync_time()

    # Stats for all upcoming events regardless of impact (next 7 days)
    all_week = EconomicEvent.get_upcoming(days=7)
    high_count = sum(1 for e in all_week if e.impact == "High")
    medium_count = sum(1 for e in all_week if e.impact == "Medium")

    return render_template(
        "index.html",
        upcoming=upcoming_high,
        today_events=today_events,
        week_events=week_events,
        market_rows=market_rows,
        last_sync=last_sync,
        country_map=Config.COUNTRY_MAP,
        impact_colors=IMPACT_COLORS,
        high_count=high_count,
        medium_count=medium_count,
        all_week_count=len(all_week),
        today=today,
    )


@app.route("/calendar")
def calendar():
    return render_template(
        "calendar.html",
        country_map=Config.COUNTRY_MAP,
        impact_colors=IMPACT_COLORS,
        country_colors=COUNTRY_COLORS,
    )


@app.route("/analysis")
def analysis():
    indicators = EconomicEvent.get_all_indicator_names()
    return render_template(
        "analysis.html",
        indicators=indicators,
        country_map=Config.COUNTRY_MAP,
        impact_colors=IMPACT_COLORS,
    )


# ---------------------------------------------------------------------------
# JSON API
# ---------------------------------------------------------------------------

@app.route("/api/events")
def api_events():
    """
    Return events in FullCalendar-compatible JSON format.
    Query params: start, end, country (multi), impact (multi)
    """
    start_str = request.args.get("start")
    end_str = request.args.get("end")
    countries = request.args.getlist("country") or None
    impacts = request.args.getlist("impact") or None

    q = EconomicEvent.query

    if start_str:
        try:
            start_dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
            q = q.filter(EconomicEvent.event_date >= start_dt)
        except ValueError:
            pass

    if end_str:
        try:
            end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
            q = q.filter(EconomicEvent.event_date <= end_dt)
        except ValueError:
            pass

    if countries:
        q = q.filter(EconomicEvent.country.in_(countries))
    if impacts:
        q = q.filter(EconomicEvent.impact.in_(impacts))

    events = q.order_by(EconomicEvent.event_date).all()

    cal_events = []
    for e in events:
        country_info = Config.COUNTRY_MAP.get(e.country, {})
        flag = country_info.get("flag", "")
        info_parts = []
        if e.forecast:
            info_parts.append(f"F: {e.forecast}")
        if e.previous:
            info_parts.append(f"P: {e.previous}")
        if e.actual:
            info_parts.append(f"A: {e.actual}")

        cal_events.append(
            {
                "id": e.id,
                "title": f"{flag} {e.title}",
                "start": e.event_date.isoformat(),
                "color": IMPACT_COLORS.get(e.impact, "#6b7280"),
                "borderColor": COUNTRY_COLORS.get(e.country, "#6b7280"),
                "extendedProps": {
                    "country": e.country,
                    "countryName": country_info.get("name", e.country),
                    "impact": e.impact,
                    "forecast": e.forecast or "—",
                    "previous": e.previous or "—",
                    "actual": e.actual or "—",
                    "info": " | ".join(info_parts) if info_parts else "No data yet",
                },
            }
        )

    return jsonify(cal_events)


@app.route("/api/market")
def api_market():
    days = min(int(request.args.get("days", 90)), 365)
    rows = MarketData.get_recent(days=days)
    return jsonify([r.to_dict() for r in rows])


@app.route("/api/analysis/<path:indicator_title>")
def api_analysis(indicator_title):
    """
    Returns historical actual-vs-forecast data for a given indicator title,
    enriched with the S&P 500 daily return on the same date.
    """
    country = request.args.get("country")
    events = EconomicEvent.get_historical(
        indicator=indicator_title, country=country, with_actual=True
    )

    results = []
    for e in events:
        surprise = _compute_surprise(e.actual, e.forecast)
        mkt = MarketData.get_for_date(e.event_date.date())

        results.append(
            {
                "date": e.event_date.date().isoformat(),
                "actual": e.actual,
                "forecast": e.forecast,
                "previous": e.previous,
                "surprise_pct": round(surprise, 3) if surprise is not None else None,
                "sp500_return": round(mkt.pct_change, 3) if mkt and mkt.pct_change is not None else None,
            }
        )

    # Simple correlation coefficient (Pearson) over matched rows
    paired = [
        (r["surprise_pct"], r["sp500_return"])
        for r in results
        if r["surprise_pct"] is not None and r["sp500_return"] is not None
    ]
    correlation = None
    if len(paired) >= 3:
        xs = [p[0] for p in paired]
        ys = [p[1] for p in paired]
        n = len(xs)
        mx = sum(xs) / n
        my = sum(ys) / n
        num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
        den_x = sum((x - mx) ** 2 for x in xs) ** 0.5
        den_y = sum((y - my) ** 2 for y in ys) ** 0.5
        if den_x > 0 and den_y > 0:
            correlation = round(num / (den_x * den_y), 4)

    return jsonify(
        {
            "indicator": indicator_title,
            "country": country,
            "data": results,
            "correlation": correlation,
            "sample_size": len(paired),
        }
    )


@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    try:
        result = _do_refresh()
        return jsonify({"status": "success", "result": result})
    except Exception as exc:
        logger.error("Manual refresh failed: %s", exc)
        return jsonify({"status": "error", "message": str(exc)}), 500


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
