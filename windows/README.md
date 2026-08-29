# earnings-bot — Windows

[English](#english) · [Русский](#русский)

Self‑contained folder: build a standalone `.exe` (or run from a venv), launch it,
and register it as an auto‑restarting Scheduled Task. Everything the bot needs at
runtime — `.env`, `data\`, `logs\`, `seed_times.json` — stays in this folder.

---

## English

### 1. Build

Needs Python 3.11+ installed (`python --version`).

```powershell
cd windows
powershell -ExecutionPolicy Bypass -File build.ps1
```

`build.ps1`:
1. creates `windows\.venv` and installs `httpx`, `python-dotenv`, `tzdata`;
2. copies `.env.example → .env` (if missing) and `..\seed_times.json`, makes `data\` and `logs\`;
3. runs PyInstaller → **`windows\dist\earnings-bot\earnings-bot.exe`** (no Python needed on the target machine).

| Command | Result |
|---------|--------|
| `build.ps1` | venv + `.exe` |
| `build.ps1 -NoExe` | venv only, run from source (skips PyInstaller) |
| `build.ps1 -Clean` | wipe `.venv` / `build` / `dist` and rebuild |

### 2. Configure

Open `windows\.env` and fill in at least:

```
TELEGRAM_TOKEN=...        # from @BotFather
TELEGRAM_CHAT_ID=@channel # bot must be a channel admin with Post + Pin rights
FINNHUB_TOKEN=...         # or leave blank and set FMP_API_KEY
```

### 3. Run

`bot.bat` uses the `.exe` if built, otherwise the source venv.

```powershell
.\bot.bat seed            # load seed_times.json
.\bot.bat once            # one sync + send pass
.\bot.bat queue           # preview the pinned-queue text
.\bot.bat pairs
.\bot.bat preview CSCO    # add --send to post it
.\run.bat                 # daemon in this console (Ctrl+C to stop)
```

### 4. Auto‑start (Scheduled Task)

```powershell
powershell -ExecutionPolicy Bypass -File .\install-task.ps1              # start at logon (no admin)
powershell -ExecutionPolicy Bypass -File .\install-task.ps1 -AtStartup   # + at boot (needs admin)
```

Task `EarningsBot`: restarts every minute on failure, no run‑time limit,
`StartWhenAvailable`. Logs: `windows\logs\earnings-bot.log` (rolled at ~5 MB, one
`.1` backup).

```powershell
Get-ScheduledTask EarningsBot | Get-ScheduledTaskInfo
Get-Content -Wait .\logs\earnings-bot.log
powershell -ExecutionPolicy Bypass -File .\uninstall-task.ps1
```

### Updating

After editing `..\bot\*.py`:

```powershell
powershell -ExecutionPolicy Bypass -File build.ps1 -Clean
Restart-ScheduledTask -TaskName EarningsBot   # if auto-start is installed
```

### Troubleshooting

* **`... cannot be loaded because running scripts is disabled`** — launch with
  `powershell -ExecutionPolicy Bypass -File build.ps1` as shown.
* **Antivirus flags `earnings-bot.exe`** — common PyInstaller false positive; add
  an exclusion, or use `build.ps1 -NoExe` (run from the venv).
* **PyInstaller doesn't support your Python** — use `build.ps1 -NoExe`, or install
  Python 3.11/3.12 for the `.exe` build.
* **`.env` / DB "not picked up"** — `bot.bat` always `cd`s into `windows\`; keep
  `.env`, `data\`, `seed_times.json` here.

---

## Русский

### 1. Собрать

Нужен установленный Python 3.11+ (`python --version`).

```powershell
cd windows
powershell -ExecutionPolicy Bypass -File build.ps1
```

`build.ps1`:
1. создаёт `windows\.venv` и ставит `httpx`, `python-dotenv`, `tzdata`;
2. копирует `.env.example → .env` (если нет) и `..\seed_times.json`, создаёт `data\` и `logs\`;
3. запускает PyInstaller → **`windows\dist\earnings-bot\earnings-bot.exe`** (Python на целевой машине не нужен).

| Команда | Результат |
|---------|-----------|
| `build.ps1` | venv + `.exe` |
| `build.ps1 -NoExe` | только venv, запуск из исходников (без PyInstaller) |
| `build.ps1 -Clean` | снести `.venv` / `build` / `dist` и пересобрать |

### 2. Настроить

Открыть `windows\.env`, заполнить как минимум:

```
TELEGRAM_TOKEN=...        # от @BotFather
TELEGRAM_CHAT_ID=@канал   # бот — админ канала с правами Post + Pin
FINNHUB_TOKEN=...         # или оставить пустым и задать FMP_API_KEY
```

### 3. Запуск

`bot.bat` берёт `.exe`, если он собран, иначе venv из исходников.

```powershell
.\bot.bat seed            # загрузить seed_times.json
.\bot.bat once            # один прогон синка + отправки
.\bot.bat queue           # посмотреть текст закреплённой очереди
.\bot.bat pairs
.\bot.bat preview CSCO    # добавить --send чтобы отправить
.\run.bat                 # демон в текущем окне (Ctrl+C — стоп)
```

### 4. Автозапуск (Планировщик задач)

```powershell
powershell -ExecutionPolicy Bypass -File .\install-task.ps1              # запуск при входе (без админа)
powershell -ExecutionPolicy Bypass -File .\install-task.ps1 -AtStartup   # + при загрузке ОС (нужен админ)
```

Задача `EarningsBot`: перезапуск раз в минуту при падении, без лимита времени,
`StartWhenAvailable`. Логи — `windows\logs\earnings-bot.log` (ролл при ~5 МБ, один
бэкап `.1`).

```powershell
Get-ScheduledTask EarningsBot | Get-ScheduledTaskInfo
Get-Content -Wait .\logs\earnings-bot.log
powershell -ExecutionPolicy Bypass -File .\uninstall-task.ps1
```

### Обновление

После правки `..\bot\*.py`:

```powershell
powershell -ExecutionPolicy Bypass -File build.ps1 -Clean
Restart-ScheduledTask -TaskName EarningsBot   # если стоит автозапуск
```

### Возможные проблемы

* **`... cannot be loaded because running scripts is disabled`** — запускать через
  `powershell -ExecutionPolicy Bypass -File build.ps1`, как выше.
* **Антивирус ругается на `earnings-bot.exe`** — типичный ложный срабат
  PyInstaller; добавьте в исключения или используйте `build.ps1 -NoExe` (запуск из
  venv).
* **PyInstaller не поддерживает вашу версию Python** — `build.ps1 -NoExe` или
  поставьте Python 3.11/3.12 для сборки `.exe`.
* **`.env` / база «не подхватываются»** — `bot.bat` всегда делает `cd` в `windows\`;
  держите `.env`, `data\`, `seed_times.json` здесь.
