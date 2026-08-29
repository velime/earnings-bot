"""Exchange registry.

MEXC is the venue that actually lists tokenized-stock perpetuals as
``<TICKER>STOCK_USDT`` — it defines the universe of tradable stock tickers.
Bitget and Gate sometimes list the popular ones too; when they do, the alert
gets an extra line for that venue.

Each entry exposes:
  fetch(timeout) -> list[{ticker, symbol, max_leverage, state}]   (state 0 = tradable)
  url(symbol, ref_code) -> str

``fetch`` / ``url`` are thin lambdas so monkeypatching e.g. ``mexc.fetch_contracts``
in tests still takes effect.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from . import bitget, gate, mexc


@dataclass(frozen=True)
class Exchange:
    name: str
    display: str
    fetch: Callable[[float], list[dict]]
    url: Callable[[str, str], str]


REGISTRY: dict[str, Exchange] = {
    "mexc": Exchange("mexc", "MEXC",
                     lambda t: mexc.fetch_contracts(t),
                     lambda s, r: mexc.pair_url(s, r)),
    "bitget": Exchange("bitget", "BITGET",
                       lambda t: bitget.fetch_contracts(t),
                       lambda s, r: bitget.pair_url(s, r)),
    "gate": Exchange("gate", "GATE",
                     lambda t: gate.fetch_contracts(t),
                     lambda s, r: gate.pair_url(s, r)),
}

# MEXC is always the source of the tradable-ticker universe, whether or not it is
# in the configured render list.
UNIVERSE = "mexc"


def active(cfg) -> list[Exchange]:
    """Configured venues that exist in the registry, in render order."""
    seen: list[Exchange] = []
    for name in cfg.exchanges:
        ex = REGISTRY.get(name)
        if ex and ex not in seen:
            seen.append(ex)
    return seen
