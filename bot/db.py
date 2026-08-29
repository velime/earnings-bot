"""SQLite storage. Schema is created idempotently on start; WAL mode."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS pairs (
    exchange      TEXT NOT NULL,
    ticker        TEXT NOT NULL,
    symbol        TEXT NOT NULL,
    max_leverage  INTEGER NOT NULL,
    base_leverage INTEGER NOT NULL,
    state         INTEGER NOT NULL DEFAULT 0,
    updated_utc   TEXT NOT NULL,
    PRIMARY KEY (exchange, ticker)
);

CREATE TABLE IF NOT EXISTS events (
    ticker        TEXT NOT NULL,
    report_date   TEXT NOT NULL,          -- YYYY-MM-DD (market local calendar day)
    session       TEXT NOT NULL,          -- bmo / amc / dmh
    year          INTEGER,
    quarter       INTEGER,
    eps_est       REAL,
    rev_est       REAL,
    yoy_pct       REAL,
    scheduled_utc TEXT NOT NULL,          -- ISO-8601 Z
    alert_utc     TEXT NOT NULL,          -- scheduled_utc - ALERT_LEAD_MINUTES
    time_source   TEXT NOT NULL,          -- override / learned / default
    sent_utc      TEXT,                   -- NULL until posted
    created_utc   TEXT NOT NULL,
    updated_utc   TEXT NOT NULL,
    PRIMARY KEY (ticker, report_date)     -- this is the dedupe guarantee
);
CREATE INDEX IF NOT EXISTS idx_events_due ON events (sent_utc, alert_utc);

CREATE TABLE IF NOT EXISTS observed_times (
    ticker       TEXT NOT NULL,
    report_date  TEXT NOT NULL,
    bucket       TEXT NOT NULL,           -- bmo / amc / dmh
    observed_utc TEXT NOT NULL,
    PRIMARY KEY (ticker, report_date)
);
CREATE INDEX IF NOT EXISTS idx_obs ON observed_times (ticker, bucket, report_date);

CREATE TABLE IF NOT EXISTS time_overrides (
    ticker      TEXT PRIMARY KEY,
    hh_mm       TEXT NOT NULL,            -- HH:MM in America/New_York
    source      TEXT NOT NULL DEFAULT 'manual',
    updated_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS yoy_cache (
    ticker         TEXT NOT NULL,
    year           INTEGER NOT NULL,
    quarter        INTEGER NOT NULL,
    revenue_actual REAL,
    fetched_utc    TEXT NOT NULL,
    PRIMARY KEY (ticker, year, quarter)
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(path: str) -> sqlite3.Connection:
    if path != ":memory:":
        Path(path).expanduser().parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30, isolation_level=None)  # autocommit
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    # `pairs` is a rebuildable cache: if an old schema without `exchange` is found,
    # drop it and let the next sync repopulate.
    cols = [r[1] for r in conn.execute("PRAGMA table_info(pairs)").fetchall()]
    if cols and "exchange" not in cols:
        conn.execute("DROP TABLE pairs")
    conn.executescript(SCHEMA)


# --------------------------------------------------------------------------- pairs
def upsert_pair(conn, exchange: str, ticker: str, symbol: str,
                max_leverage: int, state: int) -> None:
    now = _now_iso()
    conn.execute(
        """
        INSERT INTO pairs (exchange, ticker, symbol, max_leverage, base_leverage, state, updated_utc)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(exchange, ticker) DO UPDATE SET
            symbol        = excluded.symbol,
            max_leverage  = excluded.max_leverage,
            base_leverage = MAX(pairs.base_leverage, excluded.max_leverage),
            state         = excluded.state,
            updated_utc   = excluded.updated_utc
        """,
        (exchange, ticker, symbol, max_leverage, max_leverage, state, now),
    )


def get_pair(conn, exchange: str, ticker: str):
    return conn.execute(
        "SELECT * FROM pairs WHERE exchange = ? AND ticker = ?", (exchange, ticker)
    ).fetchone()


def pairs_for_ticker(conn, ticker: str):
    return conn.execute(
        "SELECT * FROM pairs WHERE ticker = ? ORDER BY exchange", (ticker,)
    ).fetchall()


def list_pairs(conn):
    return conn.execute("SELECT * FROM pairs ORDER BY ticker, exchange").fetchall()


# -------------------------------------------------------------------------- events
def upsert_event(
    conn,
    *,
    ticker: str,
    report_date: str,
    session: str,
    year,
    quarter,
    eps_est,
    rev_est,
    yoy_pct,
    scheduled_utc: str,
    alert_utc: str,
    time_source: str,
) -> None:
    """Insert or refresh a planned event. A row that was already sent is left alone."""
    existing = conn.execute(
        "SELECT sent_utc FROM events WHERE ticker = ? AND report_date = ?",
        (ticker, report_date),
    ).fetchone()
    if existing is not None and existing["sent_utc"]:
        return

    now = _now_iso()
    conn.execute(
        """
        INSERT INTO events (ticker, report_date, session, year, quarter, eps_est, rev_est,
                            yoy_pct, scheduled_utc, alert_utc, time_source, sent_utc,
                            created_utc, updated_utc)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
        ON CONFLICT(ticker, report_date) DO UPDATE SET
            session       = excluded.session,
            year          = excluded.year,
            quarter       = excluded.quarter,
            eps_est       = excluded.eps_est,
            rev_est       = excluded.rev_est,
            yoy_pct       = excluded.yoy_pct,
            scheduled_utc = excluded.scheduled_utc,
            alert_utc     = excluded.alert_utc,
            time_source   = excluded.time_source,
            updated_utc   = excluded.updated_utc
        """,
        (
            ticker, report_date, session, year, quarter, eps_est, rev_est, yoy_pct,
            scheduled_utc, alert_utc, time_source, now, now,
        ),
    )


def due_events(conn, now_iso: str):
    return conn.execute(
        "SELECT * FROM events WHERE sent_utc IS NULL AND alert_utc <= ? ORDER BY alert_utc",
        (now_iso,),
    ).fetchall()


def upcoming_events(conn):
    return conn.execute(
        "SELECT * FROM events WHERE sent_utc IS NULL ORDER BY alert_utc"
    ).fetchall()


def queue_events(conn):
    """Not-yet-alerted events, in chronological (publication) order — for the pinned queue."""
    return conn.execute(
        "SELECT * FROM events WHERE sent_utc IS NULL ORDER BY scheduled_utc, ticker"
    ).fetchall()


def delete_event(conn, ticker: str, report_date: str) -> None:
    conn.execute(
        "DELETE FROM events WHERE ticker = ? AND report_date = ? AND sent_utc IS NULL",
        (ticker, report_date),
    )


def latest_event(conn, ticker: str):
    return conn.execute(
        "SELECT * FROM events WHERE ticker = ? ORDER BY report_date DESC LIMIT 1",
        (ticker,),
    ).fetchone()


def mark_sent(conn, ticker: str, report_date: str, sent_iso: str) -> None:
    conn.execute(
        "UPDATE events SET sent_utc = ?, updated_utc = ? WHERE ticker = ? AND report_date = ?",
        (sent_iso, sent_iso, ticker, report_date),
    )


# ----------------------------------------------------------------------- overrides
def set_override(conn, ticker: str, hh_mm: str, source: str = "manual") -> None:
    conn.execute(
        """
        INSERT INTO time_overrides (ticker, hh_mm, source, updated_utc)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(ticker) DO UPDATE SET
            hh_mm       = excluded.hh_mm,
            source      = excluded.source,
            updated_utc = excluded.updated_utc
        """,
        (ticker, hh_mm, source, _now_iso()),
    )


def get_override(conn, ticker: str) -> str | None:
    row = conn.execute(
        "SELECT hh_mm FROM time_overrides WHERE ticker = ?", (ticker,)
    ).fetchone()
    return row["hh_mm"] if row else None


# ------------------------------------------------------------------ observed times
def add_observation(conn, ticker: str, report_date: str, bucket: str, observed_utc: str) -> None:
    conn.execute(
        """
        INSERT INTO observed_times (ticker, report_date, bucket, observed_utc)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(ticker, report_date) DO UPDATE SET
            bucket       = excluded.bucket,
            observed_utc = excluded.observed_utc
        """,
        (ticker, report_date, bucket, observed_utc),
    )


def recent_observations(conn, ticker: str, bucket: str, limit: int = 8):
    return conn.execute(
        """
        SELECT observed_utc FROM observed_times
        WHERE ticker = ? AND bucket = ?
        ORDER BY report_date DESC
        LIMIT ?
        """,
        (ticker, bucket, limit),
    ).fetchall()


# ------------------------------------------------------------------------ yoy cache
def get_yoy_cache(conn, ticker: str, year: int, quarter: int):
    return conn.execute(
        "SELECT revenue_actual FROM yoy_cache WHERE ticker = ? AND year = ? AND quarter = ?",
        (ticker, year, quarter),
    ).fetchone()


def get_meta(conn, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_meta(conn, key: str, value) -> None:
    conn.execute(
        """
        INSERT INTO meta (key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, None if value is None else str(value)),
    )


def set_yoy_cache(conn, ticker: str, year: int, quarter: int, revenue_actual) -> None:
    conn.execute(
        """
        INSERT INTO yoy_cache (ticker, year, quarter, revenue_actual, fetched_utc)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(ticker, year, quarter) DO UPDATE SET
            revenue_actual = excluded.revenue_actual,
            fetched_utc    = excluded.fetched_utc
        """,
        (ticker, year, quarter, revenue_actual, _now_iso()),
    )
