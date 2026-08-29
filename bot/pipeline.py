"""The data pipeline: sync (plan events), dispatch (send due alerts), refresh_queue."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from . import calendar_source, db, exchanges, formatter, telegram, timing

log = logging.getLogger(__name__)

QUEUE_META_KEY = "queue_message_id"


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(timezone.utc)


# --------------------------------------------------------------------------- pairs
def _sync_pairs(conn, cfg) -> set[str]:
    """Refresh every venue's contract list. MEXC defines the tradable-stock universe;
    other venues only contribute rows for tickers already in that universe.
    Returns the set of universe tickers."""
    universe_ex = exchanges.REGISTRY[exchanges.UNIVERSE]
    universe: set[str] = set()
    for c in universe_ex.fetch(cfg.http_timeout):
        if c["state"] == 0:
            universe.add(c["ticker"])
            db.upsert_pair(conn, universe_ex.name, c["ticker"], c["symbol"],
                           c["max_leverage"], c["state"])
    log.info("sync: %d tradable %s stock pairs", len(universe), universe_ex.display)

    for ex in exchanges.active(cfg):
        if ex.name == universe_ex.name:
            continue
        got = 0
        for c in ex.fetch(cfg.http_timeout):
            if c["state"] == 0 and c["ticker"] in universe:
                db.upsert_pair(conn, ex.name, c["ticker"], c["symbol"],
                               c["max_leverage"], c["state"])
                got += 1
        log.info("sync: %s lists %d of the universe tickers", ex.display, got)

    return universe


def _qualifying_pairs(conn, cfg, ticker: str) -> list[tuple]:
    """(Exchange, pair_row) for every active venue whose CURRENT leverage
    (max_leverage) is >= MIN_LEVERAGE, in the configured render order.
    A venue below the threshold is not shown at all; a ticker with no
    qualifying venue is dropped entirely."""
    out = []
    for ex in exchanges.active(cfg):
        pair = db.get_pair(conn, ex.name, ticker)
        if pair is not None and pair["max_leverage"] >= cfg.min_leverage:
            out.append((ex, pair))
    return out


# -------------------------------------------------------------------------- sync
def sync(conn, cfg, now: datetime | None = None) -> int:
    """One sync pass: refresh venue pairs, pull the calendar, intersect, upsert events.

    An event with no venue at >= MIN_LEVERAGE is skipped entirely (and dropped from
    the queue if it was planned before). Already-sent events are never rescheduled.
    """
    now = _now(now)
    universe = _sync_pairs(conn, cfg)

    from_date = now.date().isoformat()
    to_date = (now.date() + timedelta(days=cfg.calendar_days_ahead)).isoformat()
    rows = calendar_source.fetch_window(from_date, to_date, cfg)
    log.info("sync: %d calendar rows for %s..%s", len(rows), from_date, to_date)

    planned = skipped = bad = 0
    for row in rows:
        ticker = row["ticker"]
        if ticker not in universe:
            continue
        try:
            if not _qualifying_pairs(conn, cfg, ticker):
                db.delete_event(conn, ticker, row["date"])   # no venue >= MIN_LEVERAGE
                skipped += 1
                continue

            hh_mm, source = timing.resolve(conn, ticker, row["session"], row["date"], cfg.tz_market)
            scheduled = timing.scheduled_utc(hh_mm, row["date"], cfg.tz_market)
            alert = timing.alert_utc(scheduled, cfg.alert_lead_minutes)
            yoy = calendar_source.revenue_yoy(
                conn, ticker, row["year"], row["quarter"], row["rev_est"], cfg
            )

            db.upsert_event(
                conn,
                ticker=ticker,
                report_date=row["date"],
                session=row["session"],
                year=row["year"],
                quarter=row["quarter"],
                eps_est=row["eps_est"],
                rev_est=row["rev_est"],
                yoy_pct=yoy,
                scheduled_utc=timing.to_iso(scheduled),
                alert_utc=timing.to_iso(alert),
                time_source=source,
            )
            planned += 1
        except Exception:  # noqa: BLE001 - one bad calendar row must not abort the sync
            log.exception("sync: skipping malformed row %r", row)
            bad += 1

    log.info("sync: %d events upserted, %d skipped (leverage < %d), %d malformed",
             planned, skipped, cfg.min_leverage, bad)
    return planned


# ------------------------------------------------------------------------ render
def _render(conn, cfg, event) -> str | None:
    lines: list[str] = []
    for ex, pair in _qualifying_pairs(conn, cfg, event["ticker"]):
        url = ex.url(pair["symbol"], cfg.exchange_ref.get(ex.name, ""))
        lines.append(
            formatter.exchange_line(
                display=ex.display,
                base_leverage=pair["base_leverage"],
                max_leverage=pair["max_leverage"],
                url=url,
            )
        )
    if not lines:
        log.warning("No venue >= %dx for %s — skipping", cfg.min_leverage, event["ticker"])
        return None

    return formatter.render_message(
        ticker=event["ticker"],
        display_time=timing.display_hhmm(event["scheduled_utc"], cfg.tz_display),
        eps_est=event["eps_est"],
        rev_est=event["rev_est"],
        yoy_pct=event["yoy_pct"],
        exchange_lines=lines,
        footer_html=cfg.footer_html,
        lead_minutes=cfg.alert_lead_minutes,
    )


# ------------------------------------------------------------------------ queue
def _queue_items(conn, cfg):
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(cfg.tz_display)
    items = []
    for ev in db.queue_events(conn):
        qp = _qualifying_pairs(conn, cfg, ev["ticker"])
        if not qp:
            continue
        best = max(p["max_leverage"] for _, p in qp)
        when = timing._parse_iso(ev["scheduled_utc"]).astimezone(tz)
        items.append(
            {
                "date_dt": when,
                "hhmm": when.strftime("%H:%M"),
                "ticker": ev["ticker"],
                "leverage": best,
                "yoy_pct": ev["yoy_pct"],
            }
        )
    return items


async def refresh_queue(conn, cfg, now: datetime | None = None, client=None) -> None:
    """Post (once, pinned) or edit the channel's queue message for the next N days.
    Alerted events have sent_utc set, so they fall off automatically."""
    now = _now(now)
    text = formatter.render_queue(
        _queue_items(conn, cfg),
        updated_hhmm=timing.display_hhmm(now, cfg.tz_display),
        days_ahead=cfg.calendar_days_ahead,
        footer_html=cfg.footer_html,
    )

    mid = db.get_meta(conn, QUEUE_META_KEY)
    if mid:
        try:
            await telegram.edit_message_text(
                cfg.telegram_token, cfg.telegram_chat_id, int(mid), text, client=client
            )
            return
        except Exception as exc:  # noqa: BLE001 - message gone / not editable -> repost
            log.warning("Queue edit failed (%s) — reposting", exc)
            db.set_meta(conn, QUEUE_META_KEY, None)

    try:
        result = await telegram.send(
            cfg.telegram_token, cfg.telegram_chat_id, text, client=client
        )
    except Exception as exc:  # noqa: BLE001
        log.error("Queue post failed: %s", exc)
        return

    new_id = result.get("message_id")
    if not new_id:
        return
    db.set_meta(conn, QUEUE_META_KEY, new_id)
    if cfg.pin_queue:
        try:
            await telegram.pin_chat_message(
                cfg.telegram_token, cfg.telegram_chat_id, int(new_id), client=client
            )
        except Exception as exc:  # noqa: BLE001 - not admin / no pin right
            log.warning("Could not pin queue message: %s", exc)


# ----------------------------------------------------------------------- dispatch
async def dispatch(conn, cfg, now: datetime | None = None, client=None) -> int:
    """Send every event whose ``alert_utc <= now`` and mark it. Returns count sent.

    An alert overdue by more than ``STALE_ALERT_MINUTES`` (the bot was down) is
    skipped — marked sent without posting, so nothing goes out after the fact.
    """
    now = _now(now)
    now_iso = timing.to_iso(now)
    stale_before = now - timedelta(minutes=cfg.stale_alert_minutes)

    sent = 0
    for event in db.due_events(conn, now_iso):
        alert_dt = timing._parse_iso(event["alert_utc"])
        if alert_dt < stale_before:
            log.warning(
                "Skipping stale alert %s %s (alert_utc=%s, now=%s)",
                event["ticker"], event["report_date"], event["alert_utc"], now_iso,
            )
            db.mark_sent(conn, event["ticker"], event["report_date"], now_iso)
            continue

        text = _render(conn, cfg, event)
        if text is None:
            db.mark_sent(conn, event["ticker"], event["report_date"], now_iso)
            continue

        try:
            await telegram.send(cfg.telegram_token, cfg.telegram_chat_id, text, client=client)
        except Exception as exc:  # noqa: BLE001 - one bad send must not kill the loop
            log.error("Send failed for %s %s: %s", event["ticker"], event["report_date"], exc)
            await telegram.notify_admin(
                cfg.telegram_token,
                cfg.telegram_admin_chat_id,
                f"Send failed for {event['ticker']} {event['report_date']}: {exc}",
                client=client,
            )
            continue

        db.mark_sent(conn, event["ticker"], event["report_date"], now_iso)
        sent += 1
        log.info("Alert sent: %s %s", event["ticker"], event["report_date"])

    return sent


async def preview(conn, cfg, ticker: str, send: bool = False, client=None) -> str:
    event = db.latest_event(conn, ticker)
    if event is None:
        raise SystemExit(f"No planned event for {ticker} — run `once` or `run` first.")
    text = _render(conn, cfg, event)
    if text is None:
        raise SystemExit(f"No venue >= {cfg.min_leverage}x known for {ticker}.")
    if send:
        await telegram.send(cfg.telegram_token, cfg.telegram_chat_id, text, client=client)
    return text
