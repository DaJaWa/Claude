from datetime import datetime, timezone, timedelta
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class EconomicEvent(db.Model):
    __tablename__ = "economic_events"

    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.String(64), unique=True, nullable=False)  # MD5 dedup key
    title = db.Column(db.String(255), nullable=False)
    country = db.Column(db.String(10), nullable=False)  # USD | EUR | CNY
    event_date = db.Column(db.DateTime(timezone=True), nullable=False, index=True)
    impact = db.Column(db.String(20), nullable=False)  # High | Medium | Low
    forecast = db.Column(db.String(50))
    previous = db.Column(db.String(50))
    actual = db.Column(db.String(50))
    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    @classmethod
    def get_upcoming(cls, days=30, countries=None, impacts=None):
        now = datetime.now(timezone.utc)
        end = now + timedelta(days=days)
        q = cls.query.filter(cls.event_date >= now, cls.event_date <= end)
        if countries:
            q = q.filter(cls.country.in_(countries))
        if impacts:
            q = q.filter(cls.impact.in_(impacts))
        return q.order_by(cls.event_date).all()

    @classmethod
    def get_historical(cls, indicator=None, country=None, with_actual=True):
        now = datetime.now(timezone.utc)
        q = cls.query.filter(cls.event_date < now)
        if with_actual:
            q = q.filter(cls.actual.isnot(None), cls.actual != "")
        if indicator:
            q = q.filter(cls.title == indicator)
        if country:
            q = q.filter(cls.country == country)
        return q.order_by(cls.event_date.desc()).all()

    @classmethod
    def get_all_indicator_names(cls):
        """Return distinct (title, country) pairs that have actual values."""
        from sqlalchemy import func

        return (
            db.session.query(
                cls.title,
                cls.country,
                func.count(cls.id).label("cnt"),
            )
            .filter(cls.actual.isnot(None), cls.actual != "")
            .group_by(cls.title, cls.country)
            .order_by(func.count(cls.id).desc())
            .all()
        )

    def to_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "country": self.country,
            "event_date": self.event_date.isoformat() if self.event_date else None,
            "impact": self.impact,
            "forecast": self.forecast,
            "previous": self.previous,
            "actual": self.actual,
        }


class MarketData(db.Model):
    __tablename__ = "market_data"

    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, unique=True, nullable=False, index=True)
    open = db.Column(db.Float)
    high = db.Column(db.Float)
    low = db.Column(db.Float)
    close = db.Column(db.Float)
    volume = db.Column(db.BigInteger)
    pct_change = db.Column(db.Float)  # daily % change vs prev close

    @classmethod
    def get_recent(cls, days=90):
        from datetime import date

        cutoff = date.today() - timedelta(days=days)
        return cls.query.filter(cls.date >= cutoff).order_by(cls.date).all()

    @classmethod
    def get_for_date(cls, target_date):
        return cls.query.filter_by(date=target_date).first()

    def to_dict(self):
        return {
            "date": self.date.isoformat(),
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "pct_change": self.pct_change,
        }


class DataSync(db.Model):
    __tablename__ = "data_sync"

    id = db.Column(db.Integer, primary_key=True)
    source = db.Column(db.String(50), unique=True, nullable=False)
    last_synced = db.Column(db.DateTime(timezone=True))
    status = db.Column(db.String(20))  # success | error
    message = db.Column(db.Text)
