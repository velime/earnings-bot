"""MEXC public contract API — no API key required.

Endpoint: GET /api/v1/contract/detail
Response: {success, code, data: [{symbol, maxLeverage, state, ...}]}

Tokenized-stock perpetuals are named ``<TICKER>STOCK_USDT`` (NBISSTOCK_USDT,
CSCOSTOCK_USDT, JDSTOCK_USDT). ``state == 0`` means actively trading.
"""

from __future__ import annotations

import logging

import httpx

log = logging.getLogger(__name__)

PRIMARY_URL = "https://contract.mexc.com/api/v1/contract/detail"
FALLBACK_URL = "https://futures.mexc.com/api/v1/contract/detail"
SUFFIX = "STOCK_USDT"

_HEADERS = {"User-Agent": "earnings-bot/1.0"}


def _to_int(value, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _parse(data: list[dict]) -> list[dict]:
    """Keep only tokenized-stock contracts, strip the suffix to get the ticker."""
    out: list[dict] = []
    for item in data or []:
        symbol = str(item.get("symbol") or "")
        if not symbol.endswith(SUFFIX):
            continue
        ticker = symbol[: -len(SUFFIX)]
        if not ticker:
            continue
        out.append(
            {
                "ticker": ticker,
                "symbol": symbol,
                "max_leverage": _to_int(item.get("maxLeverage"), 0),
                "state": _to_int(item.get("state"), -1),
            }
        )
    return out


def fetch_contracts(timeout: float = 15.0) -> list[dict]:
    """Return every tokenized-stock contract MEXC currently lists.

    Never raises: on total failure it logs and returns ``[]`` so the caller
    (the sync loop) keeps running.
    """
    for url in (PRIMARY_URL, FALLBACK_URL):
        try:
            resp = httpx.get(url, timeout=timeout, headers=_HEADERS)
            resp.raise_for_status()
            payload = resp.json()
        except Exception as exc:  # noqa: BLE001 - any failure -> try the mirror
            log.warning("MEXC fetch failed for %s: %s", url, exc)
            continue

        if not payload.get("success"):
            log.warning("MEXC responded success=false (code=%s)", payload.get("code"))
            continue

        rows = _parse(payload.get("data") or [])
        log.info("MEXC: %d tokenized-stock contracts (source=%s)", len(rows), url)
        return rows

    log.error("MEXC: primary and fallback endpoints both unavailable")
    return []


def tradable_stock_pairs(contracts: list[dict]) -> dict[str, dict]:
    """``{ticker: contract}`` for contracts in the tradable state (state == 0)."""
    return {c["ticker"]: c for c in contracts if c["state"] == 0}


def pair_url(symbol: str, share_code: str = "") -> str:
    base = f"https://mexc.com/futures/{symbol}"
    return f"{base}?shareCode={share_code}" if share_code else base
