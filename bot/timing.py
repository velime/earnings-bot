"""Reconstructing the publication minute.

Free calendars give only day + session. The exact minute is chosen by priority:

  1. override  — a hand-pinned time for the ticker  (table ``time_overrides``)
  2. learned   — median of the last <=8 actually observed times for this ticker
                 in the same session; needs at least 2 observations
  3. default   — bmo -> 07:00 ET, amc -> 16:05 ET, dmh -> 12:00 ET

All internal times are HH:MM in ``America/New_York`` and converted through
``zoneinfo`` so US daylight-saving transitions are handled for free.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from statistics import median
from zoneinfo import ZoneInfo

from . import db

log = logging.getLogger(__name__)

DEFAULTS = {"bmo": "07:00", "amc": "16:05", "dmh": "12:00"}


def _parse_hhmm(value: str) -> tuple[int, int]:
    hh, mm = value.split(":")
    return int(hh), int(mm)


def _parse_iso(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def to_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _learned(conn, ticker: str, bucket: str, tz_market: str) -> str | None:
    rows = db.recent_observations(conn, ticker, bucket, limit=8)
    if len(rows) < 2:
        return None
    market = ZoneInfo(tz_market)
    minutes_of_day = []
    for row in rows:
        local = _parse_iso(row["observed_utc"]).astimezone(market)
        minutes_of_day.append(local.hour * 60 + local.minute)
    m = int(round(median(minutes_of_day)))
    return f"{m // 60:02d}:{m % 60:02d}"


def resolve(conn, ticker: str, session: str, report_date: str, tz_market: str) -> tuple[str, str]:
    """Return ``(hh_mm_et, source)`` where source is override / learned / default."""
    session = session if session in DEFAULTS else "amc"

    override = db.get_override(conn, ticker)
    if override:
        return override, "override"

    learned = _learned(conn, ticker, session, tz_market)
    if learned:
        return learned, "learned"

    return DEFAULTS[session], "default"


def scheduled_utc(hh_mm_et: str, report_date: str, tz_market: str) -> datetime:
    """Localise ``hh_mm_et`` on ``report_date`` in the market tz, return the UTC instant."""
    hh, mm = _parse_hhmm(hh_mm_et)
    day = datetime.strptime(report_date, "%Y-%m-%d").date()
    local = datetime(day.year, day.month, day.day, hh, mm, tzinfo=ZoneInfo(tz_market))
    return local.astimezone(timezone.utc)


def alert_utc(scheduled: datetime, lead_minutes: int) -> datetime:
    return scheduled - timedelta(minutes=lead_minutes)


def display_hhmm(when, tz_display: str) -> str:
    dt = _parse_iso(when) if isinstance(when, str) else when
    return dt.astimezone(ZoneInfo(tz_display)).strftime("%H:%M")
