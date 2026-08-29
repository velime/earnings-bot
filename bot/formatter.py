"""Building the alert message (parse_mode=HTML, disable_web_page_preview=true)."""

from __future__ import annotations

import html

DIVIDER = "━" * 14  # 14 heavy horizontal bars
DASH = "—"          # em dash, used for "no value"


def _esc(s) -> str:
    """Escape a value that came from an exchange / calendar API before it goes
    into an HTML message (ticker in <b>…</b>, symbol inside href="…")."""
    return html.escape(str(s), quote=True)

_WEEKDAYS_RU = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]


def fmt_eps(value) -> str:
    if value is None:
        return DASH
    return f"${value:.2f}"


def fmt_revenue(value) -> str:
    if value is None:
        return DASH
    magnitude = abs(value)
    if magnitude >= 1e9:
        return f"${value / 1e9:.2f}B"
    if magnitude >= 1e6:
        return f"${value / 1e6:.1f}M"
    if magnitude >= 1e3:
        return f"${value / 1e3:.1f}K"
    return f"${value:.0f}"


def fmt_pct(value) -> str | None:
    if value is None:
        return None
    return f"({value:+.1f}%)"


def exchange_line(*, display: str, base_leverage: int, max_leverage: int, url: str | None) -> str:
    """One venue line: '<a href=…>MEXC (100x)</a> ⚠️ плечо временно урезано до 20x'."""
    label = f"{_esc(display)} ({int(base_leverage)}x)"
    line = f'<a href="{_esc(url)}">{label}</a>' if url else label
    if max_leverage < base_leverage:
        line += f" ⚠️ плечо временно урезано до {int(max_leverage)}x"
    return line


def render_message(
    *,
    ticker: str,
    display_time: str,
    eps_est,
    rev_est,
    yoy_pct,
    exchange_lines: list[str],
    footer_html: str,
    lead_minutes: int,
) -> str:
    rev = fmt_revenue(rev_est)
    pct = fmt_pct(yoy_pct)
    rev_field = f"{rev} {pct}" if pct else rev

    msg = (
        f"⚠️ Через {lead_minutes} минут — отчёт <b>{_esc(ticker)}</b>\n"
        f"{display_time} МСК\n"
        f"EPS est: {fmt_eps(eps_est)} · Rev est: {rev_field}\n"
        f"\n"
        + "\n".join(exchange_lines)
    )
    if footer_html:
        msg += f"\n\n{DIVIDER}\n{footer_html}"
    return msg


def render_queue(items: list[dict], *, updated_hhmm: str, days_ahead: int,
                 footer_html: str) -> str:
    """The pinned queue message.

    Each item: {date_dt (aware, display tz), hhmm, ticker, leverage, yoy_pct}.
    Grouped by calendar day, chronological.
    """
    head = f"📋 Очередь отчётов · ближайшие {days_ahead} дн."
    if not items:
        body = "\nПока пусто — ждём ближайшие отчёты."
    else:
        lines: list[str] = []
        current_day = None
        for it in items:
            day = it["date_dt"].date()
            if day != current_day:
                current_day = day
                wd = _WEEKDAYS_RU[it["date_dt"].weekday()]
                lines.append(f"\n━━ {day.strftime('%d.%m')} ({wd}) ━━")
            row = f"{it['hhmm']} · {_esc(it['ticker'])} · {int(it['leverage'])}x"
            pct = fmt_pct(it.get("yoy_pct"))
            if pct:
                row += f" · {pct.strip('()')}"
            lines.append(row)
        body = "\n".join(lines)

    msg = f"{head}\n{body}\n\nОбновлено {updated_hhmm} МСК"
    if footer_html:
        msg += f"\n{DIVIDER}\n{footer_html}"
    return msg
