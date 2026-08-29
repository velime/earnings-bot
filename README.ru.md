# earnings-bot

[English](README.md) · **Русский**

Telegram-бот, который постит в **канал** алерт **за `ALERT_LEAD_MINUTES` минут**
(по умолчанию 10) до выхода квартального отчёта — но только по тем тикерам, у
которых на **MEXC** есть токенизированный stock-фьючерс (`<TICKER>STOCK_USDT`).
Если тот же тикер листят **Bitget** или **Gate**, в сообщение добавляется строка и
по этим биржам. При старте бот также постит и **закрепляет** в канале сообщение с
очередью отчётов на ближайшие N дней и поддерживает его в актуальном виде.

Бот только **информирует**. Никакой торговли.

Стек: Python 3.11+, стандартная библиотека + `httpx` + `python-dotenv`
(+ `tzdata` как база часовых поясов для `zoneinfo`). Планировщик — голый
`asyncio`. Хранилище — SQLite. Telegram — прямые вызовы Bot API через `httpx`.

---

## Как это работает

```
                 ┌──────────── scheduler.run (2 независимых asyncio-цикла) ─────────────┐
                 │                                                                      │
   каждые 30 мин │  sync loop                              send loop   каждые 20 сек    │
                 │  ─────────                              ─────────                     │
                 │  биржи: MEXC + Bitget + Gate            db.due_events(now)            │
                 │    ↓ MEXC (STOCK_USDT, state==0) —       ↓ alert_utc <= now           │
                 │      множество торгуемых тикеров;       просрочка > 20 мин?           │
                 │      Bitget/Gate лишь дополняют их       → пометить sent, НЕ постить  │
                 │  pairs (exchange, ticker → плечо)        ↓                            │
                 │    ↓                                     render + telegram.send       │
                 │  календарь: Finnhub → (пусто) → FMP      ↓                            │
                 │    ↓ нормализация: date, session, est    db.mark_sent                 │
                 │    ↓ пересечение по тикеру               ↓                            │
                 │  есть биржа с плечом ≥ MIN_LEVERAGE?     если что-то ушло →           │
                 │  нет → отчёт отбрасывается целиком         refresh_queue              │
                 │  timing.resolve → HH:MM ET                                            │
                 │  (override → learned → default)                                       │
                 │    ↓ zoneinfo: ET → UTC (переход на лето сам)                          │
                 │  scheduled_utc,  alert_utc = scheduled_utc − LEAD                      │
                 │  рост выручки г/г (кэш в SQLite)                                       │
                 │    ↓                                                                  │
                 │  db.upsert_event   PK (ticker, report_date)  ← защита от дублей       │
                 │    ↓                                                                  │
                 │  refresh_queue → post+pin / edit закреплённого сообщения-очереди       │
                 └──────────────────────────────────────────────────────────────────────┘
```

### Восстановление минуты публикации

Бесплатные календари дают только **день и сессию** (`bmo` — до открытия рынка,
`amc` — после закрытия, `dmh` — в течение сессии), но не минуту. Минута выбирается
по приоритету:

| # | Уровень | Источник | Условие |
|---|---------|----------|---------|
| 1 | **override** | таблица `time_overrides`, задаётся вручную (`override` / `seed`) | есть запись по тикеру |
| 2 | **learned** | медиана последних ≤ 8 фактически наблюдённых времён этого тикера в той же сессии | минимум 2 наблюдения |
| 3 | **default** | `bmo → 07:00 ET`, `amc → 16:05 ET`, `dmh → 12:00 ET` | всегда |

Дефолт по сессии часто мажет (JD выходит в 05:45 ET, AMAT в 16:01, CSCO в 16:05),
поэтому уровень **learned** важен: каждый раз, когда вы записываете реальное время
выхода командой `observe`, бот пересчитывает медиану и в следующий квартал попадёт
точнее. Время хранится как `HH:MM` по `America/New_York` и конвертируется через
`zoneinfo`, так что переходы на летнее время обрабатываются сами. В сообщении
показывается `Europe/Moscow`. `alert_utc = scheduled_utc − ALERT_LEAD_MINUTES`.

### Правило по плечу

На контракт хранятся два числа: `max_leverage` (текущее из API) и `base_leverage`
(максимум, который контракт когда-либо показывал — `MAX(старое, новое)`).

* Строка биржи попадает в сообщение, **только если её текущее плечо ≥ `MIN_LEVERAGE`**
  (по умолчанию 50).
* Если **ни одна** биржа не дотягивает — **отчёт игнорируется целиком**: ни алерта,
  ни строки в очереди (и удаляется из очереди, если был запланирован раньше).
* Если `max_leverage < base_leverage`, в тексте ссылки показывается `base_leverage`,
  а в конце строки — ` ⚠️ плечо временно урезано до {max_leverage}x`.

### Рост выручки г/г

`revenueEstimate` текущего квартала ÷ `revenueActual` того же квартала год назад − 1.
История берётся через Finnhub `/calendar/earnings?symbol=…` и кэшируется в SQLite
(и попадания, и промахи). Нет данных — процент просто не показывается.

### Закреплённая очередь

При старте бот постит одно сообщение — очередь отчётов на ближайшие
`CALENDAR_DAYS_AHEAD` дней — и **закрепляет** его. Дальше это же сообщение
*редактируется*: после каждого синка и после каждого алерта (тикер, по которому
только что вышло предупреждение, пропадает из очереди). id сообщения хранится в
таблице `meta`, так что после перезапуска бот правит то же сообщение, а не плодит
новые. Отключается через `PIN_QUEUE=false`.

```
📋 Очередь отчётов · ближайшие 3 дн.

━━ 01.09 (вт) ━━
23:05 · CRDO · 50x
23:05 · DELL · 100x · +12.3%
23:05 · PANW · 50x

Обновлено 00:52 МСК
```

### Формат сообщения

`parse_mode=HTML`, `disable_web_page_preview=true`. Пример с тремя биржами и
непустым `FOOTER_HTML`:

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

Форматирование чисел: EPS — `$` + 2 знака (`$-0.67`, `$1.17`). Выручка —
`$16.85B` / `$9.00B` (≥ 1B, 2 знака), `$545.1M` / `$10.2M` (≥ 1M, 1 знак).
Процент — со знаком, 1 знак (`(+14.8%)`). Нет значения — `—`. Если `FOOTER_HTML`
пуст (по умолчанию), сообщение заканчивается на блоке бирж: ни разделителя, ни
подвала.

---

## Требования

* Python **3.11+** (проверено 3.11 – 3.13).
* Всего три сторонних пакета: `httpx`, `python-dotenv`, `tzdata`
  (`tzdata` содержит базу IANA, чтобы переходы на летнее время работали на любой
  ОС, включая Windows).
* Telegram-токен и канал, где бот — **администратор** с правами
  **«Публикация сообщений»** и **«Закрепление сообщений»**.
* Ключ календаря — **Finnhub** (основной) и/или **FMP** (автоматический запасной).

---

## Быстрый старт

### Linux / macOS

```bash
git clone <адрес-вашего-форка> earnings-bot && cd earnings-bot
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env      # затем заполнить (см. «Конфигурация»)

python -m bot.main seed   # загрузить известные времена ET из seed_times.json
python -m bot.main once   # один прогон синка и отправки
python -m bot.main queue  # посмотреть текст закреплённой очереди
python -m bot.main run    # демон
```

### Windows

Готовая папка сборки — [`windows/`](windows/):

```powershell
cd windows
powershell -ExecutionPolicy Bypass -File build.ps1        # venv + standalone .exe
#   build.ps1 -NoExe   -> только venv, запуск из исходников (без PyInstaller)
#   build.ps1 -Clean   -> снести .venv / build / dist и пересобрать

notepad .env            # создаётся из .env.example при первой сборке — заполнить

.\bot.bat seed
.\bot.bat once
.\bot.bat queue
.\run.bat               # демон в текущем окне
```

`bot.bat` берёт собранный `.exe`, если он есть, иначе venv из исходников. Всё, что
боту нужно в рантайме (`.env`, `data\`, `logs\`, `seed_times.json`), лежит внутри
папки `windows\`. Подробности — [windows/README.md](windows/README.md).

---

## Конфигурация

Все настройки — переменные окружения / файл `.env`. Полный список с дефолтами — в
[`.env.example`](.env.example).

| Переменная | Смысл |
|------------|-------|
| `TELEGRAM_TOKEN` | Токен бота от [@BotFather](https://t.me/BotFather) → `/newbot`. |
| `TELEGRAM_CHAT_ID` | `@username` публичного канала или числовой `-100…` для приватного. Бот — админ канала с правами Post и Pin. |
| `TELEGRAM_ADMIN_CHAT_ID` | *(опц.)* ваш личный chat id — туда падают ошибки отправки. Не указывайте сюда публичный канал. |
| `FINNHUB_TOKEN` | Основной календарь. Ключ на <https://finnhub.io/dashboard>. При `401/403` бот логирует понятную ошибку и возвращает пусто. |
| `FMP_API_KEY` | Запасной календарь, включается сам, когда Finnhub вернул пусто. Ключ на <https://site.financialmodelingprep.com/developer/docs>. |
| `EXCHANGES` | Какие биржи показывать и в каком порядке, через запятую. По умолчанию `mexc,bitget,gate`. |
| `MEXC_SHARE_CODE` / `BITGET_REF_CODE` / `GATE_REF_CODE` | Реф-коды в ссылках на пары. Пусто — чистые ссылки. |
| `MIN_LEVERAGE` | Строка биржи показывается, только если её текущее плечо ≥ этого (по умолчанию `50`). Отчёт без такой биржи игнорируется. |
| `PIN_QUEUE` | `true` (по умолчанию) — постит и закрепляет очередь, редактирует по мере изменений; `false` — очередь выключена. |
| `ALERT_LEAD_MINUTES` | За сколько минут до выхода слать алерт (по умолчанию `10`). |
| `SYNC_INTERVAL_SECONDS` | Период цикла синка (по умолчанию `1800`). |
| `SEND_INTERVAL_SECONDS` | Период цикла отправки (по умолчанию `20`). |
| `CALENDAR_DAYS_AHEAD` | На сколько дней вперёд брать календарь / показывать очередь (по умолчанию `3`). |
| `STALE_ALERT_MINUTES` | Алерт, просроченный больше чем на столько (бот лежал), пропускается, а не постится задним числом (по умолчанию `20`). |
| `DB_PATH` | Путь к файлу SQLite (по умолчанию `data/earnings.db`). |
| `TZ_DISPLAY` / `TZ_MARKET` | Часовой пояс отображения / рынка (по умолчанию `Europe/Moscow` / `America/New_York`). |
| `HTTP_TIMEOUT` | Таймаут запроса в секундах (по умолчанию `15`). |
| `LOG_LEVEL` | `INFO` по умолчанию. URL-запросы `httpx` заглушены, чтобы токены не попадали в лог. |
| `FOOTER_HTML` | Сырой HTML после разделителя `━`×14. Пусто — без подвала. |

`seed_times.json` — словарь `{ "TICKER": "HH:MM" }` (ET). В комплекте с несколькими
стартовыми значениями для NBIS / CSCO / INFQ / JD / AMAT.

---

## Команды

```
python -m bot.main <команда>            # или в Windows:  .\bot.bat <команда>
```

| Команда | Что делает |
|---------|------------|
| `run` | демон (два asyncio-цикла) |
| `once` | один прогон синка + отправки + обновление очереди, затем выход |
| `pairs` | список пар по всем биржам с текущим/базовым плечом |
| `upcoming` | что сейчас запланировано |
| `queue [--send]` | показать текст очереди; с `--send` — запостить/обновить и закрепить |
| `preview TICKER [--send]` | показать (или отправить) сообщение по тикеру |
| `override TICKER HH:MM` | зафиксировать время публикации (ET) вручную |
| `observe TICKER DATE bucket ISO_UTC` | записать реальное время выхода, чтобы сработал уровень *learned* |
| `seed [--file seed_times.json]` | массово загрузить известные времена (как override) |

Примеры:

```bash
python -m bot.main override JD 05:45
python -m bot.main observe CSCO 2026-08-12 amc 2026-08-12T20:05:00Z
python -m bot.main preview CSCO --send
```

---

## Развёртывание

### Docker

```bash
cp .env.example .env      # заполнить
docker compose up -d --build
docker compose logs -f
```

Том `./data` хранит файл SQLite; `restart: unless-stopped`; ротация логов
json-file (`max-size=10m`, `max-file=5`). Разовые команды:
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

Юнит: `Restart=always`, `RestartSec=5` и базовая изоляция (`NoNewPrivileges`,
`ProtectSystem=strict`, `ProtectHome`, `PrivateTmp`, `Protect*`,
`RestrictNamespaces`); на запись открыт только `data/`.

### Windows (Планировщик задач)

```powershell
cd windows
powershell -ExecutionPolicy Bypass -File .\install-task.ps1              # запуск при входе
powershell -ExecutionPolicy Bypass -File .\install-task.ps1 -AtStartup   # + при загрузке ОС (админ)
```

Регистрирует задачу `EarningsBot`: перезапуск раз в минуту при падении, без лимита
времени. Логи — `windows\logs\earnings-bot.log` (ролл при ~5 МБ, один бэкап).
Снять — `.\uninstall-task.ps1`. Подробности: [windows/README.md](windows/README.md).

---

## База данных

SQLite, WAL, схема создаётся идемпотентно при старте.

| Таблица | Назначение |
|---------|------------|
| `pairs` | пары по биржам, **PK `(exchange, ticker)`**: `symbol`, `max_leverage`, `base_leverage`, `state`. Пересобираемый кэш. |
| `events` | запланированные отчёты; **PK `(ticker, report_date)`** — защита от дублей; `sent_utc` — отметка об отправке. |
| `observed_times` | реальные наблюдённые времена выхода — питает уровень *learned*. |
| `time_overrides` | ручные / засеянные времена `HH:MM` ET; высший приоритет. |
| `yoy_cache` | кэш `revenueActual` по `(ticker, year, quarter)`. |
| `meta` | пары ключ→значение; хранит `queue_message_id` (закреплённое сообщение). |

---

## Надёжность

* **Telegram**: ретраи с уважением к `429` `retry_after` (с потолком) и бэкофф на
  `5xx` / сетевые ошибки; провал отправки логируется и (если задан) уходит админу.
  «message is not modified» при правке очереди считается успехом.
* **Источники данных**: падение любой биржи (MEXC / Bitget / Gate) или календаря
  (Finnhub / FMP) логируется и трактуется как «нет данных» — процесс не умирает.
  Одна битая строка календаря пропускается, а не роняет проход. Оба цикла
  планировщика ловят свои исключения и продолжают работу.
* **Просроченные алерты**: если бот лежал и алерт просрочен больше чем на
  `STALE_ALERT_MINUTES`, событие помечается отправленным, но **не** постится.
* **Дубли**: `PK (ticker, report_date)`; уже отправленные события не
  перепланируются.
* **Секреты**: логирование URL-запросов `httpx` заглушено, чтобы ключ Finnhub и
  токен Telegram не попадали в файл лога.

---

## Лицензия

[MIT](LICENSE).
