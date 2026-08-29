"""CLI entry point:  python -m bot.main <cmd>

  run                                 daemon (two asyncio loops)
  once                                one sync pass + one send pass
  pairs                               list MEXC tokenized-stock pairs with leverage
  upcoming                            what is currently scheduled
  queue [--send]                      show / post the pinned queue for the next N days
  preview TICKER [--send]             render (or send) the message for a ticker
  override TICKER HH:MM               pin a publication time (ET)
  observe TICKER DATE bucket ISO_UTC  record an actual release so the bot learns
  seed [--file seed_times.json]       bulk-load known times (as overrides)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from . import db, pipeline, scheduler, timing
from .config import load_config


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    # httpx/httpcore log full request URLs at INFO — those carry the Finnhub
    # ?token= and the Telegram /bot<TOKEN>/ path. Keep them out of the logs.
    for noisy in ("httpx", "httpcore", "hpack"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _conn(cfg):
    conn = db.connect(cfg.db_path)
    db.init_db(conn)
    return conn


# --------------------------------------------------------------------------- cmds
def cmd_run(cfg, args) -> None:
    asyncio.run(scheduler.run(cfg))


def cmd_once(cfg, args) -> None:
    conn = _conn(cfg)
    try:
        pipeline.sync(conn, cfg)

        async def _go():
            await pipeline.dispatch(conn, cfg)
            await pipeline.refresh_queue(conn, cfg)

        asyncio.run(_go())
    finally:
        conn.close()


def cmd_queue(cfg, args) -> None:
    conn = _conn(cfg)
    try:
        if args.send:
            asyncio.run(pipeline.refresh_queue(conn, cfg))
            print("(queue posted / refreshed)")
        else:
            from . import formatter, timing
            from datetime import datetime, timezone
            text = formatter.render_queue(
                pipeline._queue_items(conn, cfg),
                updated_hhmm=timing.display_hhmm(datetime.now(timezone.utc), cfg.tz_display),
                days_ahead=cfg.calendar_days_ahead,
                footer_html=cfg.footer_html,
            )
            print(text)
    finally:
        conn.close()


def cmd_pairs(cfg, args) -> None:
    conn = _conn(cfg)
    try:
        rows = db.list_pairs(conn)
        if not rows:
            pipeline._sync_pairs(conn, cfg)
            rows = db.list_pairs(conn)
        if not rows:
            print("(no pairs — exchanges unreachable?)")
            return
        for r in rows:
            flag = ""
            if r["max_leverage"] < r["base_leverage"]:
                flag = f"   ⚠️ cut {r['base_leverage']}x -> {r['max_leverage']}x"
            print(f"{r['exchange']:<7} {r['ticker']:<8} {r['symbol']:<22} "
                  f"{r['max_leverage']:>4}x  (base {r['base_leverage']}x){flag}")
    finally:
        conn.close()


def cmd_upcoming(cfg, args) -> None:
    conn = _conn(cfg)
    try:
        rows = db.upcoming_events(conn)
        if not rows:
            print("(nothing scheduled)")
            return
        for r in rows:
            msk = timing.display_hhmm(r["scheduled_utc"], cfg.tz_display)
            print(f"{r['ticker']:<8} {r['report_date']} {r['session']:<3}  "
                  f"report {msk} МСК  alert {r['alert_utc']}  [{r['time_source']}]")
    finally:
        conn.close()


def cmd_preview(cfg, args) -> None:
    conn = _conn(cfg)
    try:
        text = asyncio.run(pipeline.preview(conn, cfg, args.ticker.upper(), send=args.send))
        print(text)
        if args.send:
            print("\n(sent)")
    finally:
        conn.close()


def _validate_hhmm(value: str) -> str:
    hh, mm = value.split(":")
    datetime(2000, 1, 1, int(hh), int(mm))  # raises ValueError if out of range
    return f"{int(hh):02d}:{int(mm):02d}"


def cmd_override(cfg, args) -> None:
    conn = _conn(cfg)
    try:
        hh_mm = _validate_hhmm(args.hh_mm)
        db.set_override(conn, args.ticker.upper(), hh_mm, "manual")
        print(f"override: {args.ticker.upper()} -> {hh_mm} ET")
    finally:
        conn.close()


def cmd_observe(cfg, args) -> None:
    conn = _conn(cfg)
    try:
        observed = timing._parse_iso(args.iso_utc).astimezone(timezone.utc)
        db.add_observation(
            conn,
            args.ticker.upper(),
            args.date,
            args.bucket,
            observed.strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        print(f"observed: {args.ticker.upper()} {args.date} {args.bucket} "
              f"{observed.strftime('%Y-%m-%dT%H:%M:%SZ')}")
    finally:
        conn.close()


def cmd_seed(cfg, args) -> None:
    conn = _conn(cfg)
    try:
        path = Path(args.file)
        data = json.loads(path.read_text(encoding="utf-8"))
        n = 0
        for ticker, hh_mm in data.items():
            db.set_override(conn, ticker.upper(), _validate_hhmm(str(hh_mm)), "seed")
            n += 1
        print(f"seeded {n} times from {path}")
    finally:
        conn.close()


_COMMANDS = {
    "run": cmd_run,
    "once": cmd_once,
    "pairs": cmd_pairs,
    "upcoming": cmd_upcoming,
    "queue": cmd_queue,
    "preview": cmd_preview,
    "override": cmd_override,
    "observe": cmd_observe,
    "seed": cmd_seed,
}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m bot.main")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("run", help="run the daemon")
    sub.add_parser("once", help="one sync + one send pass")
    sub.add_parser("pairs", help="list MEXC tokenized-stock pairs")
    sub.add_parser("upcoming", help="list scheduled alerts")

    q = sub.add_parser("queue", help="show the pinned-queue text (--send to post/refresh it)")
    q.add_argument("--send", action="store_true")

    pp = sub.add_parser("preview", help="render/send the message for a ticker")
    pp.add_argument("ticker")
    pp.add_argument("--send", action="store_true", help="also post it to the channel")

    po = sub.add_parser("override", help="pin a publication time (ET)")
    po.add_argument("ticker")
    po.add_argument("hh_mm", metavar="HH:MM")

    ob = sub.add_parser("observe", help="record an actual release time")
    ob.add_argument("ticker")
    ob.add_argument("date", metavar="YYYY-MM-DD")
    ob.add_argument("bucket", choices=["bmo", "amc", "dmh"])
    ob.add_argument("iso_utc", metavar="ISO_UTC")

    sd = sub.add_parser("seed", help="bulk-load known times")
    sd.add_argument("--file", default="seed_times.json")

    return p


def main(argv=None) -> None:
    cfg = load_config()
    _setup_logging(cfg.log_level)
    args = build_parser().parse_args(argv)
    _COMMANDS[args.cmd](cfg, args)


if __name__ == "__main__":
    main()
