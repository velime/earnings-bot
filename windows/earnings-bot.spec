# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec — run via  build.ps1  (from the windows\ folder).
import os

from PyInstaller.utils.hooks import collect_all

ROOT = os.path.abspath(os.path.join(SPECPATH, os.pardir))  # noqa: F821 (SPECPATH injected)

datas, binaries, hiddenimports = [], [], []
for pkg in ("tzdata", "certifi"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

datas += [(os.path.join(ROOT, "seed_times.json"), ".")]

hiddenimports += [
    "bot", "bot.config", "bot.db", "bot.mexc", "bot.bitget", "bot.gate",
    "bot.exchanges", "bot.calendar_source", "bot.timing", "bot.formatter",
    "bot.telegram", "bot.pipeline", "bot.scheduler", "bot.main",
    "httpx", "httpcore", "h11", "anyio", "sniffio", "idna", "certifi",
    "dotenv", "zoneinfo",
]

a = Analysis(
    ["entry.py"],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "pytest", "PyInstaller"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="earnings-bot",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="earnings-bot",
)
