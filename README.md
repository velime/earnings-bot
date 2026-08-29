# earnings-bot

**English** · [Русский](README.ru.md)

A Telegram bot that posts an alert **`ALERT_LEAD_MINUTES` before** (10 by default) a
company's quarterly earnings release — but only for tickers that have a
tokenized‑stock perpetual future on **MEXC** (`<TICKER>STOCK_USDT`). If the same
ticker is also listed on **Bitget** or **Gate**, an extra line is added for that
venue. On start the bot also posts and **pins** a "queue for the next N days"
message in the channel and keeps it up to date.

The bot only **informs**. It never trades.

Stack: Python 3.11+, standard library + `httpx` + `python-dotenv` (+ `tzdata` as the
time‑zone database for `zoneinfo`). Scheduler: bare `asyncio`. Storage: SQLite.
Telegram: direct Bot API calls over `httpx`.

---

## How it works

```
                 ┌───────────── scheduler.run (2 independent asyncio loops) ─────────────┐
                 │                                                                       │
   every 30 min  │  sync loop                                 send loop   every 20 s     │
                 │  ─────────                                 ─────────                   │
                 │  exchanges: MEXC + Bitget + Gate           db.due_events(now)          │
                 │    ↓ MEXC (STOCK_USDT, state==0) = the      ↓ alert_utc <= now         │
                 │      universe of tradable tickers;         overdue > 20 min?           │
                 │      Bitget/Gate only enrich those          → mark sent, do NOT post   │
                 │  pairs (exchange, ticker → leverage)        ↓                          │
                 │    ↓                                        render + telegram.send     │
                 │  calendar: Finnhub → (empty) → FMP          ↓                          │
                 │    ↓ normalize: date, session, EPS/rev est  db.mark_sent               │
                 │    ↓ intersect by ticker                    ↓                          │
                 │  any venue with leverage ≥ MIN_LEVERAGE?    if any sent → refresh_queue│
                 │  no → drop the report entirely              │                          │
                 │  timing.resolve → HH:MM ET                                              │
                 │  (override → learned → default)                                         │
                 │    ↓ zoneinfo: ET → UTC (DST handled)                                   │
                 │  scheduled_utc,  alert_utc = scheduled_utc − LEAD                       │
                 │  revenue YoY (cached in SQLite)                                         │
                 │    ↓                                                                    │
                 │  db.upsert_event   PK (ticker, report_date)  ← dedupe                   │
                 │    ↓                                                                    │
                 │  refresh_queue → post+pin / edit the pinned queue message               │
                 └───────────────────────────────────────────────────────────────────────┘
```

### Reconstructing the publication minute

Free calendars only give a **day + a session bucket** (`bmo` before market open,
`amc` after close, `dmh` during market hours) — never the exact minute. The minute
is chosen by priority:

| # | Level | Source | Condition |
|---|-------|--------|-----------|
| 1 | **override** | `time_overrides` table, set by hand (`override` / `seed`) | a row exists for the ticker |
| 2 | **learned** | median of the last ≤ 8 **observed** release times for this ticker in the same session | at least 2 observations |
| 3 | **default** | `bmo → 07:00 ET`, `amc → 16:05 ET`, `dmh → 12:00 ET` | always |

The session default is often wrong (JD reports at 05:45 ET, AMAT at 16:01, CSCO at
16:05), so the **learned** level matters: every time you record a real release time
with `observe`, the bot recomputes the median and lands closer next quarter. Times
are stored as `HH:MM` in `America/New_York` and converted through `zoneinfo`, so US
daylight‑saving transitions are handled automatically. Messages show
`Europe/Moscow`. `alert_utc = scheduled_utc − ALERT_LEAD_MINUTES`.

### Leverage rule

Each contract keeps two numbers: `max_leverage` (current, from the API) and
`base_leverage` (the highest it has ever shown — `MAX(old, new)`).

* A venue line appears **only if its current leverage ≥ `MIN_LEVERAGE`** (default 50).
* If **no** venue is at or above the threshold, the report is **ignored entirely** —
  no alert, no queue entry (and it is removed from the queue if planned earlier).
* If `max_leverage < base_leverage`, the link text shows `base_leverage` and the line
  gets a suffix: ` ⚠️ плечо временно урезано до {max_leverage}x`.

### Revenue YoY

`revenueEstimate` of the current quarter ÷ `revenueActual` of the same quarter one
year ago − 1. History comes from Finnhub `/calendar/earnings?symbol=…` and is cached
in SQLite (hits **and** misses). No data → the percentage is simply omitted.

### The pinned queue

On start the bot posts one message — the earnings queue for the next
`CALENDAR_DAYS_AHEAD` days — and **pins** it. That same message is then *edited*:
after every sync and after every alert (the ticker that was just alerted drops off).
The message id is stored in the `meta` table, so after a restart the bot edits the
same message instead of posting a new one. Disable with `PIN_QUEUE=false`.

```
📋 Очередь отчётов · ближайшие 3 дн.

━━ 01.09 (вт) ━━
23:05 · CRDO · 50x
23:05 · DELL · 100x · +12.3%
23:05 · PANW · 50x

Обновлено 00:52 МСК
```

### Message format

`parse_mode=HTML`, `disable_web_page_preview=true`. Example with three venues and a
non‑empty `FOOTER_HTML`:

```
⚠️ Через 10 минут — отчёт <b>CSCO</b>
23:05 МСК
EPS est: $1.17 · Rev est: $16.85B (+14.8%)

<a href="https://mexc.com/futures/CSCOSTOCK_USDT">MEXC (100x)</a> ⚠️ плечо временно урезано до 60x
<a href="https://www.bitget.com/futures/usdt/CSCOUSDT">BITGET (50x)</a>
<a href="https://www.gate.io/futures/USDT/CSCO_USDT">GATE (75x)</a>

━━━━━━━━━━━━━━
<a href="https://t.me/your_channel">Календарь отчётов</a>
```

Number formatting: EPS — `$` + 2 decimals (`$-0.67`, `$1.17`). Revenue —
`$16.85B` / `$9.00B` (≥ 1B, 2 decimals), `$545.1M` / `$10.2M` (≥ 1M, 1 decimal).
Percent — signed, 1 decimal (`(+14.8%)`). Missing value — `—`. If `FOOTER_HTML` is
empty (the default), the message ends after the venue block: no divider, no footer.

---

## Requirements

* Python **3.11+** (3.11 – 3.13 tested).
* Only three third‑party packages: `httpx`, `python-dotenv`, `tzdata`
  (`tzdata` ships the IANA time‑zone database so DST math works on any host,
  including Windows).
* A Telegram bot token and a channel where the bot is an **administrator** with
  **"Post messages"** and **"Pin messages"** rights.
* A calendar API key — **Finnhub** (primary) and/or **FMP** (automatic fallback).

---

## Quick start

### Linux / macOS

```bash
git clone <your-fork-url> earnings-bot && cd earnings-bot
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env      # then fill it in (see Configuration)

python -m bot.main seed   # load known ET times from seed_times.json
python -m bot.main once   # one sync + send pass, prints nothing on success
python -m bot.main queue  # preview the pinned-queue text
python -m bot.main run    # run the daemon
```

### Windows

A ready‑made build folder lives in [`windows/`](windows/):

```powershell
cd windows
powershell -ExecutionPolicy Bypass -File build.ps1        # venv + standalone .exe
#   build.ps1 -NoExe   -> venv only, run from source (no PyInstaller)
#   build.ps1 -Clean   -> wipe .venv / build / dist and rebuild

notepad .env            # created from .env.example on first build — fill it in

.\bot.bat seed
.\bot.bat once
.\bot.bat queue
.\run.bat               # daemon in this console
```

`bot.bat` picks the built `.exe` if present, otherwise the source venv. Everything
the bot needs at runtime (`.env`, `data\`, `logs\`, `seed_times.json`) stays inside
the `windows\` folder. See [windows/README.md](windows/README.md) for details.

### Tests (no network, all stubbed)

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

---

## Configuration

All settings come from environment variables / a `.env` file. Full list with
defaults is in [`.env.example`](.env.example).

| Variable | Meaning |
|----------|---------|
| `TELEGRAM_TOKEN` | Bot token from [@BotFather](https://t.me/BotFather) → `/newbot`. |
| `TELEGRAM_CHAT_ID` | `@channelusername` (public) or numeric `-100…` (private). The bot must be a channel admin with **Post** and **Pin** rights. |
| `TELEGRAM_ADMIN_CHAT_ID` | *(optional)* your private chat id — send‑failure notices go here. Do **not** point it at the public channel. |
| `FINNHUB_TOKEN` | Primary calendar. Key from <https://finnhub.io/dashboard>. On `401/403` the bot logs a clear message and returns empty. |
| `FMP_API_KEY` | Fallback calendar, used automatically when Finnhub returns nothing. Key from <https://site.financialmodelingprep.com/developer/docs>. |
| `EXCHANGES` | Venues to show and their order, comma‑separated. Default `mexc,bitget,gate`. |
| `MEXC_SHARE_CODE` / `BITGET_REF_CODE` / `GATE_REF_CODE` | Referral codes appended to venue links. Leave blank for clean links. |
| `MIN_LEVERAGE` | A venue is shown only if its current leverage ≥ this (default `50`). A report with no such venue is ignored. |
| `PIN_QUEUE` | `true` (default) posts + pins the queue message and edits it as things change; `false` disables the queue. |
| `ALERT_LEAD_MINUTES` | Minutes before the release to fire the alert (default `10`). |
| `SYNC_INTERVAL_SECONDS` | Sync loop period (default `1800`). |
| `SEND_INTERVAL_SECONDS` | Send loop period (default `20`). |
| `CALENDAR_DAYS_AHEAD` | How many days forward to pull the calendar / show in the queue (default `3`). |
| `STALE_ALERT_MINUTES` | An alert overdue by more than this (the bot was down) is skipped, not posted late (default `20`). |
| `DB_PATH` | SQLite file path (default `data/earnings.db`). |
| `TZ_DISPLAY` / `TZ_MARKET` | Display / market time zones (default `Europe/Moscow` / `America/New_York`). |
| `HTTP_TIMEOUT` | Per‑request timeout in seconds (default `15`). |
| `LOG_LEVEL` | `INFO` by default. Request URLs from `httpx` are silenced so tokens never reach the log. |
| `FOOTER_HTML` | Raw HTML appended after a `━`×14 divider. Empty = no footer. |

`seed_times.json` is a `{ "TICKER": "HH:MM" }` map (ET). Bundled with a few starter
values for NBIS / CSCO / INFQ / JD / AMAT.

---

## Commands

```
python -m bot.main <command>            # or, on Windows:  .\bot.bat <command>
```

| Command | Description |
|---------|-------------|
| `run` | the daemon (two asyncio loops) |
| `once` | one sync pass + one send pass + refresh the queue, then exit |
| `pairs` | list pairs across all venues with current/base leverage |
| `upcoming` | what is currently scheduled |
| `queue [--send]` | print the pinned‑queue text; `--send` posts / refreshes and pins it |
| `preview TICKER [--send]` | render (or send) the alert message for a ticker |
| `override TICKER HH:MM` | pin a publication time (ET) by hand |
| `observe TICKER DATE bucket ISO_UTC` | record a real release time so the *learned* level kicks in |
| `seed [--file seed_times.json]` | bulk‑load known times (as overrides) |

Examples:

```bash
python -m bot.main override JD 05:45
python -m bot.main observe CSCO 2026-08-12 amc 2026-08-12T20:05:00Z
python -m bot.main preview CSCO --send
```

---

## Deployment

### Docker

```bash
cp .env.example .env      # fill it in
docker compose up -d --build
docker compose logs -f
```

The `./data` volume holds the SQLite file; `restart: unless-stopped`; JSON‑file log
rotation (`max-size=10m`, `max-file=5`). One‑off commands:
`docker compose run --rm earnings-bot pairs`.

### systemd (Linux)

```bash
sudo useradd --system --home /opt/earnings-bot --shell /usr/sbin/nologin earnings
sudo mkdir -p /opt/earnings-bot/data
sudo rsync -a --exclude .venv --exclude data --exclude windows ./ /opt/earnings-bot/
cd /opt/earnings-bot
sudo -u earnings python3 -m venv .venv
sudo -u earnings .venv/bin/pip install -r requirements.txt
sudo -u earnings cp .env.example .env && sudo -u earnings nano .env
sudo chmod 600 /opt/earnings-bot/.env
sudo chown -R earnings:earnings /opt/earnings-bot

sudo cp earnings-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now earnings-bot
journalctl -u earnings-bot -f
```

The unit sets `Restart=always`, `RestartSec=5` and basic isolation
(`NoNewPrivileges`, `ProtectSystem=strict`, `ProtectHome`, `PrivateTmp`,
`Protect*`, `RestrictNamespaces`); only `data/` is writable.

### Windows (Scheduled Task)

```powershell
cd windows
powershell -ExecutionPolicy Bypass -File .\install-task.ps1              # start at logon
powershell -ExecutionPolicy Bypass -File .\install-task.ps1 -AtStartup   # + at boot (admin)
```

Registers task `EarningsBot`: restarts every minute on failure, no run‑time limit.
Logs go to `windows\logs\earnings-bot.log` (rolled at ~5 MB, one backup).
Remove with `.\uninstall-task.ps1`. Details: [windows/README.md](windows/README.md).

---

## Data & storage

SQLite, WAL mode, schema created idempotently on start.

| Table | Purpose |
|-------|---------|
| `pairs` | venue pairs, **PK `(exchange, ticker)`**: `symbol`, `max_leverage`, `base_leverage`, `state`. A rebuildable cache. |
| `events` | planned reports; **PK `(ticker, report_date)`** — the dedupe guarantee; `sent_utc` marks a posted alert. |
| `observed_times` | real observed release times — feeds the *learned* level. |
| `time_overrides` | manual / seeded `HH:MM` ET times; highest priority. |
| `yoy_cache` | cached `revenueActual` per `(ticker, year, quarter)`. |
| `meta` | key→value; holds `queue_message_id` (the pinned message). |

---

## Reliability

* **Telegram**: retries respect `429` `retry_after` (capped) and back off on `5xx` /
  transport errors; a failed send is logged and (if configured) forwarded to the
  admin chat. "message is not modified" on queue edits is treated as success.
* **Data sources**: any venue (MEXC / Bitget / Gate) or calendar (Finnhub / FMP)
  failure is logged and treated as "no data" — the process never dies. A single
  malformed calendar row is skipped, not fatal. Both scheduler loops catch their
  own exceptions and keep going.
* **Late alerts**: if the bot was down and an alert is more than
  `STALE_ALERT_MINUTES` overdue, the event is marked sent but **not** posted.
* **Dedupe**: `PK (ticker, report_date)`; already‑sent events are never rescheduled.
* **Secrets**: `httpx` request‑URL logging is silenced so the Finnhub key and
  Telegram token never land in the log file.

---

## License

[MIT](LICENSE).
