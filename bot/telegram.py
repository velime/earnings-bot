"""Telegram Bot API via direct httpx calls (no aiogram / PTB).

Retries respect 429 ``retry_after`` and use exponential backoff on 5xx / transport
errors. Send failures are logged and optionally forwarded to the admin chat.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

log = logging.getLogger(__name__)

_API = "https://api.telegram.org/bot{token}/{method}"
_MAX_RETRIES = 5
_MAX_BACKOFF = 30.0


async def _call(client: httpx.AsyncClient, token: str, method: str, payload: dict) -> dict:
    url = _API.format(token=token, method=method)
    backoff = 1.0

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            resp = await client.post(url, json=payload, timeout=20.0)
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            log.warning("Telegram %s transport error (try %d/%d): %s",
                        method, attempt, _MAX_RETRIES, exc)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _MAX_BACKOFF)
            continue

        if resp.status_code == 429:
            retry_after = 1
            try:
                retry_after = int(resp.json().get("parameters", {}).get("retry_after", 1))
            except Exception:  # noqa: BLE001
                pass
            retry_after = max(1, min(retry_after, 300))  # never stall the loop for long
            log.warning("Telegram %s 429 — waiting %ss", method, retry_after)
            await asyncio.sleep(retry_after + 0.5)
            continue

        if resp.status_code >= 500:
            log.warning("Telegram %s HTTP %d (try %d/%d)",
                        method, resp.status_code, attempt, _MAX_RETRIES)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _MAX_BACKOFF)
            continue

        data = resp.json()
        if not data.get("ok"):
            desc = str(data.get("description", ""))
            # editing to identical text is not an error for our purposes
            if "message is not modified" in desc.lower():
                return {"not_modified": True}
            raise RuntimeError(f"Telegram API error ({resp.status_code}): {desc}")
        return data["result"]

    raise RuntimeError(f"Telegram {method} failed after {_MAX_RETRIES} attempts")


async def send(token: str, chat_id: str, text: str, *,
               disable_preview: bool = True, client: httpx.AsyncClient | None = None) -> dict:
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": disable_preview,
    }
    owns_client = client is None
    client = client or httpx.AsyncClient()
    try:
        return await _call(client, token, "sendMessage", payload)
    finally:
        if owns_client:
            await client.aclose()


async def edit_message_text(token: str, chat_id: str, message_id: int, text: str, *,
                            disable_preview: bool = True,
                            client: httpx.AsyncClient | None = None) -> dict:
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": disable_preview,
    }
    owns_client = client is None
    client = client or httpx.AsyncClient()
    try:
        return await _call(client, token, "editMessageText", payload)
    finally:
        if owns_client:
            await client.aclose()


async def pin_chat_message(token: str, chat_id: str, message_id: int, *,
                           disable_notification: bool = True,
                           client: httpx.AsyncClient | None = None) -> dict:
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "disable_notification": disable_notification,
    }
    owns_client = client is None
    client = client or httpx.AsyncClient()
    try:
        return await _call(client, token, "pinChatMessage", payload)
    finally:
        if owns_client:
            await client.aclose()


async def notify_admin(token: str, admin_chat_id: str | None, text: str,
                       client: httpx.AsyncClient | None = None) -> None:
    if not admin_chat_id:
        return
    try:
        await send(token, admin_chat_id, text, client=client)
    except Exception as exc:  # noqa: BLE001 - admin notification must never raise
        log.error("Could not notify admin: %s", exc)
