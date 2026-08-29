"""Gate.io public USDT-perp contract list — no API key required.

GET https://api.gateio.ws/api/v4/futures/usdt/contracts
Response: [{name:"AAPL_USDT", leverage_max:"20", in_delisting:false, trade_status:"tradable", ...}]

As with Bitget, stock perps are not tagged; :mod:`bot.pipeline` filters by the
MEXC stock universe.
"""

from __future__ import annotations

import logging

import httpx

log = logging.getLogger(__name__)

API_URL = "https://api.gateio.ws/api/v4/futures/usdt/contracts"
_HEADERS = {"Accept": "application/json", "User-Agent": "earnings-bot/1.0"}


def _to_int(value, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def fetch_contracts(timeout: float = 15.0) -> list[dict]:
    try:
        resp = httpx.get(API_URL, timeout=timeout, headers=_HEADERS)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        log.warning("Gate fetch failed: %s", exc)
        return []

    out: list[dict] = []
    for it in data or []:
        name = str(it.get("name") or "")
        if not name.endswith("_USDT"):
            continue
        ticker = name[: -len("_USDT")]
        if not ticker:
            continue
        status = str(it.get("trade_status") or "tradable").lower()
        delisting = bool(it.get("in_delisting"))
        state = 0 if (status == "tradable" and not delisting) else 1
        out.append(
            {
                "ticker": ticker,
                "symbol": name,
                "max_leverage": _to_int(it.get("leverage_max")),
                "state": state,
            }
        )
    log.info("Gate: %d USDT perps", len(out))
    return out


def pair_url(symbol: str, ref: str = "") -> str:
    base = f"https://www.gate.io/futures/USDT/{symbol}"
    return f"{base}?ref={ref}" if ref else base
