"""Two independent asyncio loops. Each survives its own exceptions and never
brings the process down.

  sync loop : one run at start (then pins/refreshes the channel queue),
              then every SYNC_INTERVAL_SECONDS (default 1800)
  send loop : every SEND_INTERVAL_SECONDS (default 20); refreshes the queue
              whenever it actually posted an alert
"""

from __future__ import annotations

import asyncio
import logging
import signal

import httpx

from . import db, pipeline

log = logging.getLogger(__name__)


async def _interval(stop: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
    except asyncio.TimeoutError:
        pass


async def _sync_loop(conn, cfg, stop: asyncio.Event, client: httpx.AsyncClient) -> None:
    while not stop.is_set():
        try:
            pipeline.sync(conn, cfg)
            await pipeline.refresh_queue(conn, cfg, client=client)
        except Exception:  # noqa: BLE001
            log.exception("sync loop iteration failed — continuing")
        await _interval(stop, cfg.sync_interval_seconds)


async def _send_loop(conn, cfg, stop: asyncio.Event, client: httpx.AsyncClient) -> None:
    while not stop.is_set():
        try:
            n = await pipeline.dispatch(conn, cfg, client=client)
            if n:
                await pipeline.refresh_queue(conn, cfg, client=client)
        except Exception:  # noqa: BLE001
            log.exception("send loop iteration failed — continuing")
        await _interval(stop, cfg.send_interval_seconds)


async def run(cfg) -> None:
    conn = db.connect(cfg.db_path)
    db.init_db(conn)

    stop = asyncio.Event()
    client = httpx.AsyncClient()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, AttributeError):
            pass  # Windows / restricted environments

    tasks = [
        asyncio.create_task(_sync_loop(conn, cfg, stop, client), name="sync"),
        asyncio.create_task(_send_loop(conn, cfg, stop, client), name="send"),
    ]
    log.info("earnings-bot started (sync=%ss, send=%ss, lead=%smin, min_leverage=%dx)",
             cfg.sync_interval_seconds, cfg.send_interval_seconds,
             cfg.alert_lead_minutes, cfg.min_leverage)

    try:
        await stop.wait()
    finally:
        stop.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        await client.aclose()
        conn.close()
        log.info("earnings-bot stopped")
