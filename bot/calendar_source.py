"""Free earnings calendars.

Primary : Finnhub   /calendar/earnings?from=&to=&token=
Fallback: FMP       /api/v3/earning_calendar?from=&to=&apikey=   (auto, when Finnhub is empty)

Both give only a day + a session bucket (bmo / amc / dmh), never the exact minute.
The minute is reconstructed in :mod:`bot.timing`.

Year-over-year revenue growth uses Finnhub's per-symbol history, cached in SQLite.
"""

from __future__ import annotations

import logging

import httpx

from . import db

log = logging.getLogger(__name__)

FINNHUB_URL = "https://finnhub.io/api/v1/calendar/earnings"
FMP_URL = "https://financialmodelingprep.com/api/v3/earning_calendar"

_SESSION_KEYWORDS = {
    "bmo": "bmo",
    "before market open": "bmo",
    "amc": "amc",
    "after market close": "amc",
    "dmh": "dmh",
    "during market hours": "dmh",
}


def _session_from_time(hh: int, mm: int) -> str:
    minutes = hh * 60 + mm
    if minutes < 9 * 60 + 30:
        return "bmo"
    if minutes >= 16 * 60:
        return "amc"
    return "dmh"


def _session(raw) -> str:
    """Normalise anything a calendar might hand us into bmo / amc / dmh."""
    if raw is None:
        return "amc"
    s = str(raw).strip().lower()
    if not s:
        return "amc"
    for keyword, bucket in _SESSION_KEYWORDS.items():
        if keyword in s:
            return bucket
    if ":" in s:  # FMP occasionally returns "HH:MM[:SS]"
        parts = s.split(":")
        try:
            return _session_from_time(int(parts[0]), int(parts[1][:2]))
        except (ValueError, IndexError):
            pass
    return "amc"


def _to_float(value):
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- Finnhub
def _fetch_finnhub(from_date: str, to_date: str, token: str, timeout: float) -> list[dict]:
    resp = httpx.get(
        FINNHUB_URL,
        params={"from": from_date, "to": to_date, "token": token},
        timeout=timeout,
    )
    if resp.status_code in (401, 403):
        log.error(
            "Finnhub rejected the calendar request (HTTP %s). The earnings calendar "
            "endpoint needs a paid plan or a valid token — returning empty.",
            resp.status_code,
        )
        return []
    resp.raise_for_status()

    rows: list[dict] = []
    for it in resp.json().get("earningsCalendar") or []:
        rows.append(
            {
                "date": it.get("date"),
                "ticker": (it.get("symbol") or "").upper(),
                "session": _session(it.get("hour")),
                "eps_est": _to_float(it.get("epsEstimate")),
                "rev_est": _to_float(it.get("revenueEstimate")),
                "year": _to_int(it.get("year")),
                "quarter": _to_int(it.get("quarter")),
            }
        )
    return [r for r in rows if r["date"] and r["ticker"]]


# ------------------------------------------------------------------------------- FMP
def _fetch_fmp(from_date: str, to_date: str, apikey: str, timeout: float) -> list[dict]:
    resp = httpx.get(
        FMP_URL,
        params={"from": from_date, "to": to_date, "apikey": apikey},
        timeout=timeout,
    )
    if resp.status_code in (401, 403):
        log.error("FMP rejected the calendar request (HTTP %s) — returning empty.", resp.status_code)
        return []
    resp.raise_for_status()

    rows: list[dict] = []
    for it in resp.json() or []:
        rows.append(
            {
                "date": it.get("date"),
                "ticker": (it.get("symbol") or "").upper(),
                "session": _session(it.get("time")),
                "eps_est": _to_float(it.get("epsEstimated")),
                "rev_est": _to_float(it.get("revenueEstimated")),
                "year": None,
                "quarter": None,
            }
        )
    return [r for r in rows if r["date"] and r["ticker"]]


def fetch_window(from_date: str, to_date: str, cfg) -> list[dict]:
    """Normalised earnings rows for the inclusive ``[from_date, to_date]`` window.

    Finnhub first; FMP is tried automatically when Finnhub returns nothing.
    Any source failure is logged and treated as "no rows" — it never propagates.
    """
    rows: list[dict] = []

    if cfg.finnhub_token:
        try:
            rows = _fetch_finnhub(from_date, to_date, cfg.finnhub_token, cfg.http_timeout)
        except Exception as exc:  # noqa: BLE001
            log.warning("Finnhub calendar failed: %s", exc)
    else:
        log.info("FINNHUB_TOKEN not set — skipping the primary calendar")

    if not rows and cfg.fmp_api_key:
        log.info("Primary calendar returned nothing — falling back to FMP")
        try:
            rows = _fetch_fmp(from_date, to_date, cfg.fmp_api_key, cfg.http_timeout)
        except Exception as exc:  # noqa: BLE001
            log.warning("FMP calendar failed: %s", exc)

    return rows


# ------------------------------------------------------------------ revenue YoY
def _revenue_actual(conn, ticker: str, year: int, quarter: int, cfg):
    cached = db.get_yoy_cache(conn, ticker, year, quarter)
    if cached is not None:
        return cached["revenue_actual"]

    if not cfg.finnhub_token:
        return None

    value = None
    try:
        resp = httpx.get(
            FINNHUB_URL,
            params={"symbol": ticker, "token": cfg.finnhub_token},
            timeout=cfg.http_timeout,
        )
        if resp.status_code in (401, 403):
            log.error("Finnhub history rejected (HTTP %s) for %s", resp.status_code, ticker)
            return None  # transient/auth — do not cache
        resp.raise_for_status()
        for it in resp.json().get("earningsCalendar") or []:
            if _to_int(it.get("year")) == year and _to_int(it.get("quarter")) == quarter:
                value = _to_float(it.get("revenueActual"))
                break
    except Exception as exc:  # noqa: BLE001
        log.warning("Finnhub history failed for %s: %s", ticker, exc)
        return None  # transient — do not cache

    # cache the result of a *successful* lookup, hit or miss, so a ticker with no
    # history is not re-queried on every sync (historical data won't change)
    db.set_yoy_cache(conn, ticker, year, quarter, value)
    return value


def revenue_yoy(conn, ticker: str, year, quarter, rev_est, cfg) -> float | None:
    """``(rev_est / revenue_actual_same_quarter_one_year_ago - 1) * 100``.

    Returns ``None`` whenever any input is missing — the caller then just omits
    the percentage from the message.
    """
    if not rev_est or year is None or quarter is None:
        return None
    actual = _revenue_actual(conn, ticker, int(year) - 1, int(quarter), cfg)
    if not actual:
        return None
    return (rev_est / actual - 1.0) * 100.0
