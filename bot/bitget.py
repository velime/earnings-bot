"""Bitget public USDT-perp contract list — no API key required.

GET https://api.bitget.com/api/v2/mix/market/contracts?productType=usdt-futures
Response: {code:"00000", data:[{symbol:"AAPLUSDT", maxLever:"20", symbolStatus:"normal", ...}]}

Bitget does not tag which perps are tokenized stocks, so this returns every USDT
perp; :mod:`bot.pipeline` keeps only tickers that MEXC lists as stock futures.
"""

from __future__ import annotations

import logging

import httpx

log = logging.getLogger(__name__)

API_URL = "https://api.bitget.com/api/v2/mix/market/contracts"
_HEADERS = {"User-Agent": "earnings-bot/1.0"}


def _to_int(value, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def fetch_contracts(timeout: float = 15.0) -> list[dict]:
    try:
        resp = httpx.get(
            API_URL,
            params={"productType": "usdt-futures"},
            timeout=timeout,
            headers=_HEADERS,
        )
        resp.raise_for_status()
        payload = resp.json()
    except Exception as exc:  # noqa: BLE001
        log.warning("Bitget fetch failed: %s", exc)
        return []

    if str(payload.get("code")) not in ("00000", "0"):
        log.warning("Bitget code=%s msg=%s", payload.get("code"), payload.get("msg"))
        return []

    out: list[dict] = []
    for it in payload.get("data") or []:
        symbol = str(it.get("symbol") or "")
        if not symbol.endswith("USDT"):
            continue
        ticker = symbol[: -len("USDT")]
        if not ticker:
            continue
        status = str(it.get("symbolStatus") or it.get("status") or "").lower()
        state = 0 if status in ("", "normal", "listed", "online", "trading") else 1
        out.append(
            {
                "ticker": ticker,
                "symbol": symbol,
                "max_leverage": _to_int(it.get("maxLever") or it.get("maxLeverage")),
                "state": state,
            }
        )
    log.info("Bitget: %d USDT perps", len(out))
    return out


def pair_url(symbol: str, ref: str = "") -> str:
    base = f"https://www.bitget.com/futures/usdt/{symbol}"
    return f"{base}?ref={ref}" if ref else base
