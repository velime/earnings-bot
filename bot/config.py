"""Configuration, loaded once from the environment / .env file."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import find_dotenv, load_dotenv

# Prefer a .env in the working directory (walking up), then fall back to the
# default search relative to this package. Real env vars still win over the file.
load_dotenv(find_dotenv(usecwd=True) or find_dotenv(), override=False)

# No default footer: the message ends after the exchange block unless FOOTER_HTML is set.
DEFAULT_FOOTER = ""
DEFAULT_EXCHANGES = "mexc,bitget,gate"


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw.strip())
    except ValueError:
        return default


def _csv(name: str, default: str) -> tuple[str, ...]:
    raw = os.getenv(name)
    raw = raw if raw and raw.strip() else default
    return tuple(x.strip().lower() for x in raw.split(",") if x.strip())


@dataclass(frozen=True)
class Config:
    telegram_token: str
    telegram_chat_id: str            # channel: "@channelusername" or "-100…"; bot must be an admin
    telegram_admin_chat_id: str | None

    finnhub_token: str | None
    fmp_api_key: str | None

    exchanges: tuple[str, ...]       # active venues, in render order
    exchange_ref: dict = field(default_factory=dict)   # venue -> referral / share code

    min_leverage: int = 50           # a venue line is shown only if its base leverage >= this;
                                     # an event with no qualifying venue is dropped entirely
    pin_queue: bool = True           # keep a pinned "queue for the next N days" message

    alert_lead_minutes: int = 10
    sync_interval_seconds: int = 1800
    send_interval_seconds: int = 20
    calendar_days_ahead: int = 3
    stale_alert_minutes: int = 20

    db_path: str = "data/earnings.db"
    footer_html: str = ""
    tz_display: str = "Europe/Moscow"
    tz_market: str = "America/New_York"
    http_timeout: float = 15.0
    log_level: str = "INFO"


def load_config() -> Config:
    exchanges = _csv("EXCHANGES", DEFAULT_EXCHANGES)
    exchange_ref = {
        "mexc": (os.getenv("MEXC_SHARE_CODE") or os.getenv("MEXC_REF_CODE") or "").strip(),
        "bitget": (os.getenv("BITGET_REF_CODE") or "").strip(),
        "gate": (os.getenv("GATE_REF_CODE") or "").strip(),
    }
    return Config(
        telegram_token=os.getenv("TELEGRAM_TOKEN", ""),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
        telegram_admin_chat_id=(os.getenv("TELEGRAM_ADMIN_CHAT_ID") or "").strip() or None,
        finnhub_token=(os.getenv("FINNHUB_TOKEN") or "").strip() or None,
        fmp_api_key=(os.getenv("FMP_API_KEY") or "").strip() or None,
        exchanges=exchanges,
        exchange_ref=exchange_ref,
        min_leverage=_int("MIN_LEVERAGE", 50),
        pin_queue=(os.getenv("PIN_QUEUE", "true").strip().lower() not in ("0", "false", "no", "off")),
        alert_lead_minutes=_int("ALERT_LEAD_MINUTES", 10),
        sync_interval_seconds=_int("SYNC_INTERVAL_SECONDS", 1800),
        send_interval_seconds=_int("SEND_INTERVAL_SECONDS", 20),
        calendar_days_ahead=_int("CALENDAR_DAYS_AHEAD", 3),
        stale_alert_minutes=_int("STALE_ALERT_MINUTES", 20),
        db_path=os.getenv("DB_PATH", "data/earnings.db"),
        footer_html=os.getenv("FOOTER_HTML", DEFAULT_FOOTER),
        tz_display=os.getenv("TZ_DISPLAY", "Europe/Moscow"),
        tz_market=os.getenv("TZ_MARKET", "America/New_York"),
        http_timeout=_float("HTTP_TIMEOUT", 15.0),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
    )
