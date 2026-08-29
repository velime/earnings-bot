"""End-to-end pipeline test on stubs — no network.

Stubbed:  mexc.fetch_contracts, bitget.fetch_contracts, gate.fetch_contracts,
          calendar_source.fetch_window, calendar_source.revenue_yoy, telegram.send

Reference data from the old channel's archive; the reconstructed publication
minute must match to the exact minute:

  NBIS 2026-08-12 bmo  eps -0.67  rev 545.1M           -> 14:03 МСК, 50x
  CSCO 2026-08-12 amc  eps  1.17  rev 16.85B  +14.8%   -> 23:05 МСК, 100x cut to 20x
  INFQ 2026-08-12 amc  eps -0.07  rev 10.2M            -> 23:05 МСК, 50x cut to 20x
  JD   2026-08-13 bmo  eps  0.86  rev 51.55B  +3.5%    -> 12:45 МСК, 100x, NO cut
  AMAT 2026-08-13 amc  eps  3.38  rev 9.00B   +23.3%   -> 23:01 МСК, 100x
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from bot import bitget, calendar_source, db, formatter, gate, mexc, pipeline, telegram
from bot.config import Config

ROOT = Path(__file__).resolve().parents[1]

FULL_LEVERAGE = {"NBIS": 50, "CSCO": 100, "INFQ": 50, "JD": 100, "AMAT": 100}
# CSCO later trimmed 100 -> 60 (still >= MIN_LEVERAGE, so it keeps the "urezano" note)
CURRENT_LEVERAGE = {"NBIS": 50, "CSCO": 60, "INFQ": 50, "JD": 100, "AMAT": 100}


def _mexc_contracts(levmap: dict[str, int]) -> list[dict]:
    rows = [
        {"ticker": t, "symbol": f"{t}STOCK_USDT", "max_leverage": lev, "state": 0}
        for t, lev in levmap.items()
    ]
    rows.append({"ticker": "BTC", "symbol": "BTC_USDT", "max_leverage": 200, "state": 0})
    rows.append({"ticker": "HALT", "symbol": "HALTSTOCK_USDT", "max_leverage": 10, "state": 3})
    return rows


def _perp_contracts(levmap: dict[str, int], suffix: str) -> list[dict]:
    # a Bitget/Gate-style list: mostly crypto noise + a few stock tickers
    rows = [
        {"ticker": t, "symbol": f"{t}{suffix}", "max_leverage": lev, "state": 0}
        for t, lev in levmap.items()
    ]
    rows.append({"ticker": "ETH", "symbol": f"ETH{suffix}", "max_leverage": 125, "state": 0})
    return rows


CALENDAR = [
    {"date": "2026-08-12", "ticker": "NBIS", "session": "bmo",
     "eps_est": -0.67, "rev_est": 545_100_000.0, "year": 2026, "quarter": 2},
    {"date": "2026-08-12", "ticker": "CSCO", "session": "amc",
     "eps_est": 1.17, "rev_est": 16_850_000_000.0, "year": 2026, "quarter": 4},
    {"date": "2026-08-12", "ticker": "INFQ", "session": "amc",
     "eps_est": -0.07, "rev_est": 10_200_000.0, "year": 2026, "quarter": 2},
    {"date": "2026-08-13", "ticker": "JD", "session": "bmo",
     "eps_est": 0.86, "rev_est": 51_550_000_000.0, "year": 2026, "quarter": 2},
    {"date": "2026-08-13", "ticker": "AMAT", "session": "amc",
     "eps_est": 3.38, "rev_est": 9_000_000_000.0, "year": 2026, "quarter": 3},
    {"date": "2026-08-12", "ticker": "MSFT", "session": "amc",  # no MEXC stock pair
     "eps_est": 3.0, "rev_est": 70_000_000_000.0, "year": 2026, "quarter": 4},
]

YOY = {"CSCO": 14.8, "JD": 3.5, "AMAT": 23.3}
NOW = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)

MEXC_REF, BITGET_REF, GATE_REF = "mexc-ref", "bg-ref", "gt-ref"


@pytest.fixture
def cfg(tmp_path) -> Config:
    return Config(
        telegram_token="TESTTOKEN",
        telegram_chat_id="@somechannel",
        telegram_admin_chat_id=None,
        finnhub_token="x",
        fmp_api_key=None,
        exchanges=("mexc", "bitget", "gate"),
        exchange_ref={"mexc": MEXC_REF, "bitget": BITGET_REF, "gate": GATE_REF},
        min_leverage=50,
        pin_queue=True,
        alert_lead_minutes=10,
        sync_interval_seconds=1800,
        send_interval_seconds=20,
        calendar_days_ahead=3,
        stale_alert_minutes=20,
        db_path=str(tmp_path / "t.db"),
        footer_html="",
        tz_display="Europe/Moscow",
        tz_market="America/New_York",
        http_timeout=5.0,
        log_level="WARNING",
    )


@pytest.fixture
def conn(cfg):
    c = db.connect(cfg.db_path)
    db.init_db(c)
    yield c
    c.close()


class _Outbox(list):
    """List of sent alert messages, plus .edits / .pins for the queue calls."""
    edits: list
    pins: list


@pytest.fixture
def sent(monkeypatch) -> _Outbox:
    outbox = _Outbox()
    outbox.edits = []
    outbox.pins = []

    async def fake_send(token, chat_id, text, **kw):
        outbox.append({"chat_id": chat_id, "text": text})
        return {"message_id": 1000 + len(outbox)}

    async def fake_edit(token, chat_id, message_id, text, **kw):
        outbox.edits.append({"message_id": message_id, "text": text})
        return {"message_id": message_id}

    async def fake_pin(token, chat_id, message_id, **kw):
        outbox.pins.append(message_id)
        return {"ok": True}

    monkeypatch.setattr(telegram, "send", fake_send)
    monkeypatch.setattr(telegram, "edit_message_text", fake_edit)
    monkeypatch.setattr(telegram, "pin_chat_message", fake_pin)
    monkeypatch.setattr(calendar_source, "fetch_window", lambda *a, **k: [dict(r) for r in CALENDAR])
    monkeypatch.setattr(
        calendar_source, "revenue_yoy",
        lambda conn, ticker, year, quarter, rev_est, cfg: YOY.get(ticker),
    )
    # by default the other venues list nothing
    monkeypatch.setattr(bitget, "fetch_contracts", lambda timeout=15.0: [])
    monkeypatch.setattr(gate, "fetch_contracts", lambda timeout=15.0: [])
    return outbox


def _seed_overrides(conn):
    seed = json.loads((ROOT / "seed_times.json").read_text(encoding="utf-8"))
    for ticker, hh_mm in seed.items():
        db.set_override(conn, ticker, hh_mm, "seed")


def _plan(conn, cfg, monkeypatch):
    monkeypatch.setattr(mexc, "fetch_contracts", lambda timeout=15.0: _mexc_contracts(FULL_LEVERAGE))
    pipeline.sync(conn, cfg, now=NOW)
    _seed_overrides(conn)
    monkeypatch.setattr(mexc, "fetch_contracts",
                        lambda timeout=15.0: _mexc_contracts(CURRENT_LEVERAGE))
    pipeline.sync(conn, cfg, now=NOW)


def _fire_all(conn, cfg):
    for ev in list(db.upcoming_events(conn)):
        asyncio.run(pipeline.dispatch(conn, cfg, now=pipeline.timing._parse_iso(ev["alert_utc"])))


def _by_ticker(outbox):
    out = {}
    for msg in outbox:
        head = msg["text"].splitlines()[0]
        if "<b>" not in head:      # skip the queue message
            continue
        out[head.split("<b>")[1].split("</b>")[0]] = msg["text"]
    return out


def _last_queue_text(sent):
    """The most recent queue text — from edits if any, else the first-post send."""
    if sent.edits:
        return sent.edits[-1]["text"]
    for msg in sent:
        if msg["text"].startswith("📋"):
            return msg["text"]
    return None


# --------------------------------------------------------------------------- tests
def test_reference_week_mexc_only_no_footer(cfg, conn, sent, monkeypatch):
    _plan(conn, cfg, monkeypatch)
    assert {r["ticker"] for r in db.upcoming_events(conn)} == {"NBIS", "CSCO", "INFQ", "JD", "AMAT"}

    _fire_all(conn, cfg)
    msgs = _by_ticker(sent)

    assert msgs["CSCO"] == "\n".join([
        "⚠️ Через 10 минут — отчёт <b>CSCO</b>",
        "23:05 МСК",
        "EPS est: $1.17 · Rev est: $16.85B (+14.8%)",
        "",
        '<a href="https://mexc.com/futures/CSCOSTOCK_USDT?shareCode=mexc-ref">MEXC (100x)</a>'
        " ⚠️ плечо временно урезано до 60x",
    ])

    assert "\n14:03 МСК\n" in msgs["NBIS"]
    assert "EPS est: $-0.67 · Rev est: $545.1M\n" in msgs["NBIS"]
    assert msgs["NBIS"].rstrip().endswith(
        '<a href="https://mexc.com/futures/NBISSTOCK_USDT?shareCode=mexc-ref">MEXC (50x)</a>'
    )
    assert "урезано" not in msgs["NBIS"]

    assert "\n23:05 МСК\n" in msgs["INFQ"]
    assert "EPS est: $-0.07 · Rev est: $10.2M\n" in msgs["INFQ"]
    assert msgs["INFQ"].rstrip().endswith(
        '<a href="https://mexc.com/futures/INFQSTOCK_USDT?shareCode=mexc-ref">MEXC (50x)</a>'
    )
    assert "урезано" not in msgs["INFQ"]

    assert "\n12:45 МСК\n" in msgs["JD"]
    assert "EPS est: $0.86 · Rev est: $51.55B (+3.5%)\n" in msgs["JD"]
    assert ">MEXC (100x)</a>" in msgs["JD"] and "урезано" not in msgs["JD"]

    assert "\n23:01 МСК\n" in msgs["AMAT"]
    assert "EPS est: $3.38 · Rev est: $9.00B (+23.3%)\n" in msgs["AMAT"]
    assert ">MEXC (100x)</a>" in msgs["AMAT"] and "урезано" not in msgs["AMAT"]

    # no divider / footer when FOOTER_HTML is empty
    assert "━" not in msgs["AMAT"]


def test_multiple_exchange_lines(cfg, conn, sent, monkeypatch):
    monkeypatch.setattr(bitget, "fetch_contracts",
                        lambda timeout=15.0: _perp_contracts({"CSCO": 50, "AMAT": 75}, "USDT"))
    monkeypatch.setattr(gate, "fetch_contracts",
                        lambda timeout=15.0: _perp_contracts({"CSCO": 75}, "_USDT"))
    _plan(conn, cfg, monkeypatch)
    _fire_all(conn, cfg)
    msgs = _by_ticker(sent)

    csco_lines = msgs["CSCO"].split("\n\n")[1].split("\n")
    assert csco_lines == [
        '<a href="https://mexc.com/futures/CSCOSTOCK_USDT?shareCode=mexc-ref">MEXC (100x)</a>'
        " ⚠️ плечо временно урезано до 60x",
        '<a href="https://www.bitget.com/futures/usdt/CSCOUSDT?ref=bg-ref">BITGET (50x)</a>',
        '<a href="https://www.gate.io/futures/USDT/CSCO_USDT?ref=gt-ref">GATE (75x)</a>',
    ]

    # AMAT is on MEXC + Bitget only
    amat_lines = msgs["AMAT"].split("\n\n")[1].split("\n")
    assert amat_lines == [
        '<a href="https://mexc.com/futures/AMATSTOCK_USDT?shareCode=mexc-ref">MEXC (100x)</a>',
        '<a href="https://www.bitget.com/futures/usdt/AMATUSDT?ref=bg-ref">BITGET (75x)</a>',
    ]

    # NBIS is MEXC-only
    assert msgs["NBIS"].split("\n\n")[1].count("\n") == 0


def test_exchange_render_order_follows_config(cfg, conn, sent, monkeypatch):
    object.__setattr__(cfg, "exchanges", ("gate", "mexc"))  # bitget disabled, gate first
    monkeypatch.setattr(gate, "fetch_contracts",
                        lambda timeout=15.0: _perp_contracts({"CSCO": 75}, "_USDT"))
    monkeypatch.setattr(bitget, "fetch_contracts",
                        lambda timeout=15.0: _perp_contracts({"CSCO": 50}, "USDT"))
    _plan(conn, cfg, monkeypatch)
    _fire_all(conn, cfg)
    msgs = _by_ticker(sent)
    lines = msgs["CSCO"].split("\n\n")[1].split("\n")
    assert lines[0].startswith('<a href="https://www.gate.io/')
    assert lines[1].startswith('<a href="https://mexc.com/')
    assert not any("bitget" in ln for ln in lines)


def test_footer_is_rendered_when_set(cfg, conn, sent, monkeypatch):
    object.__setattr__(cfg, "footer_html", '<a href="https://t.me/mychan">Календарь отчётов</a>')
    _plan(conn, cfg, monkeypatch)
    _fire_all(conn, cfg)
    msgs = _by_ticker(sent)
    assert msgs["AMAT"].endswith(
        "\n\n" + "━" * 14 + '\n<a href="https://t.me/mychan">Календарь отчётов</a>'
    )


def test_stale_alert_is_skipped_not_posted(cfg, conn, sent, monkeypatch):
    _plan(conn, cfg, monkeypatch)
    ev = next(r for r in db.upcoming_events(conn) if r["ticker"] == "CSCO")
    late = pipeline.timing._parse_iso(ev["alert_utc"]) + timedelta(minutes=25)
    asyncio.run(pipeline.dispatch(conn, cfg, now=late))
    assert sent == []
    assert all(r["ticker"] != "CSCO" for r in db.upcoming_events(conn))


def test_sent_event_is_not_replanned(cfg, conn, sent, monkeypatch):
    _plan(conn, cfg, monkeypatch)
    ev = next(r for r in db.upcoming_events(conn) if r["ticker"] == "AMAT")
    fire_at = pipeline.timing._parse_iso(ev["alert_utc"])
    asyncio.run(pipeline.dispatch(conn, cfg, now=fire_at))
    assert len(sent) == 1
    pipeline.sync(conn, cfg, now=NOW)
    asyncio.run(pipeline.dispatch(conn, cfg, now=fire_at))
    assert len(sent) == 1


def test_leverage_ratchets_up_per_exchange(cfg, conn, sent, monkeypatch):
    monkeypatch.setattr(bitget, "fetch_contracts",
                        lambda timeout=15.0: _perp_contracts({"CSCO": 50}, "USDT"))
    _plan(conn, cfg, monkeypatch)
    csco_mexc = db.get_pair(conn, "mexc", "CSCO")
    assert (csco_mexc["base_leverage"], csco_mexc["max_leverage"]) == (100, 60)
    csco_bg = db.get_pair(conn, "bitget", "CSCO")
    assert (csco_bg["base_leverage"], csco_bg["max_leverage"]) == (50, 50)
    nbis = db.get_pair(conn, "mexc", "NBIS")
    assert (nbis["base_leverage"], nbis["max_leverage"]) == (50, 50)


def test_low_leverage_venue_line_is_dropped(cfg, conn, sent, monkeypatch):
    # Bitget lists CSCO at 30x (< MIN_LEVERAGE) -> its line must not appear
    monkeypatch.setattr(bitget, "fetch_contracts",
                        lambda timeout=15.0: _perp_contracts({"CSCO": 30, "AMAT": 60}, "USDT"))
    _plan(conn, cfg, monkeypatch)
    _fire_all(conn, cfg)
    msgs = _by_ticker(sent)
    assert "BITGET" not in msgs["CSCO"]          # 30x dropped
    assert ">MEXC (100x)</a>" in msgs["CSCO"]
    assert "BITGET (60x)" in msgs["AMAT"]        # 60x kept


def test_event_with_no_venue_ge_min_is_ignored(cfg, conn, sent, monkeypatch):
    low = {"NBIS": 20, "CSCO": 100, "INFQ": 50, "JD": 100, "AMAT": 100}
    monkeypatch.setattr(mexc, "fetch_contracts", lambda timeout=15.0: _mexc_contracts(low))
    pipeline.sync(conn, cfg, now=NOW)
    _seed_overrides(conn)
    pipeline.sync(conn, cfg, now=NOW)

    planned = {r["ticker"] for r in db.upcoming_events(conn)}
    assert "NBIS" not in planned                 # 20x everywhere -> dropped
    assert {"CSCO", "INFQ", "JD", "AMAT"} <= planned

    _fire_all(conn, cfg)
    assert "NBIS" not in _by_ticker(sent)


def test_leverage_drop_removes_a_previously_planned_event(cfg, conn, sent, monkeypatch):
    monkeypatch.setattr(mexc, "fetch_contracts", lambda timeout=15.0: _mexc_contracts(FULL_LEVERAGE))
    pipeline.sync(conn, cfg, now=NOW)
    assert any(r["ticker"] == "NBIS" for r in db.upcoming_events(conn))

    dropped = dict(FULL_LEVERAGE); dropped["NBIS"] = 10
    monkeypatch.setattr(mexc, "fetch_contracts", lambda timeout=15.0: _mexc_contracts(dropped))
    pipeline.sync(conn, cfg, now=NOW)
    assert not any(r["ticker"] == "NBIS" for r in db.upcoming_events(conn))


def test_queue_is_posted_pinned_then_edited(cfg, conn, sent, monkeypatch):
    _plan(conn, cfg, monkeypatch)

    asyncio.run(pipeline.refresh_queue(conn, cfg, now=NOW))
    q = _last_queue_text(sent)
    assert q.startswith("📋 Очередь отчётов · ближайшие 3 дн.")
    assert "━━ 12.08 (ср) ━━" in q and "━━ 13.08 (чт) ━━" in q
    assert "23:05 · CSCO · 60x · +14.8%" in q
    assert "14:03 · NBIS · 50x" in q
    assert "Обновлено" in q
    # pinned exactly once, id remembered
    assert len(sent.pins) == 1
    assert db.get_meta(conn, pipeline.QUEUE_META_KEY) == str(sent.pins[0])

    # a second refresh edits the same message, does not repost / repin
    posts_before = len(sent)
    asyncio.run(pipeline.refresh_queue(conn, cfg, now=NOW))
    assert len(sent) == posts_before
    assert len(sent.pins) == 1
    assert len(sent.edits) == 1


def test_alerted_ticker_leaves_the_queue(cfg, conn, sent, monkeypatch):
    _plan(conn, cfg, monkeypatch)
    asyncio.run(pipeline.refresh_queue(conn, cfg, now=NOW))
    assert "NBIS" in _last_queue_text(sent)

    # fire only the earliest alert (NBIS) at its own time; the rest aren't due yet
    nbis = next(r for r in db.upcoming_events(conn) if r["ticker"] == "NBIS")
    at = pipeline.timing._parse_iso(nbis["alert_utc"])
    asyncio.run(pipeline.dispatch(conn, cfg, now=at))
    asyncio.run(pipeline.refresh_queue(conn, cfg, now=at))

    q = _last_queue_text(sent)
    assert "NBIS" not in q
    assert "CSCO" in q and "AMAT" in q          # others still listed


def test_mexc_parse_strips_suffix_and_filters_state():
    data = [
        {"symbol": "NBISSTOCK_USDT", "maxLeverage": 50, "state": 0},
        {"symbol": "CSCOSTOCK_USDT", "maxLeverage": 100, "state": 0},
        {"symbol": "BTC_USDT", "maxLeverage": 200, "state": 0},
        {"symbol": "XYZSTOCK_USDT", "maxLeverage": 20, "state": 3},
    ]
    parsed = mexc._parse(data)
    assert {p["ticker"] for p in parsed} == {"NBIS", "CSCO", "XYZ"}
    assert set(mexc.tradable_stock_pairs(parsed)) == {"NBIS", "CSCO"}


def test_number_formatting():
    assert formatter.fmt_eps(-0.67) == "$-0.67"
    assert formatter.fmt_eps(1.17) == "$1.17"
    assert formatter.fmt_eps(None) == "—"
    assert formatter.fmt_revenue(16_850_000_000) == "$16.85B"
    assert formatter.fmt_revenue(9_000_000_000) == "$9.00B"
    assert formatter.fmt_revenue(545_100_000) == "$545.1M"
    assert formatter.fmt_revenue(10_200_000) == "$10.2M"
    assert formatter.fmt_revenue(None) == "—"
    assert formatter.fmt_pct(14.8) == "(+14.8%)"
    assert formatter.fmt_pct(-3.2) == "(-3.2%)"
    assert formatter.fmt_pct(None) is None


def test_learned_level_beats_default_and_override_beats_learned(cfg, conn):
    db.add_observation(conn, "FOO", "2026-05-01", "amc", "2026-05-01T20:01:00Z")
    db.add_observation(conn, "FOO", "2026-08-01", "amc", "2026-08-01T20:03:00Z")
    hh_mm, source = pipeline.timing.resolve(conn, "FOO", "amc", "2026-08-13", cfg.tz_market)
    assert (hh_mm, source) == ("16:02", "learned")

    db.set_override(conn, "FOO", "09:30", "manual")
    hh_mm, source = pipeline.timing.resolve(conn, "FOO", "amc", "2026-08-13", cfg.tz_market)
    assert (hh_mm, source) == ("09:30", "override")


def test_single_observation_falls_back_to_default(cfg, conn):
    db.add_observation(conn, "BAR", "2026-08-01", "bmo", "2026-08-01T11:00:00Z")
    hh_mm, source = pipeline.timing.resolve(conn, "BAR", "bmo", "2026-08-13", cfg.tz_market)
    assert (hh_mm, source) == ("07:00", "default")
