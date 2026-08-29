"""PyInstaller entry point — wraps bot.main:main()."""

import multiprocessing
import sys

from bot.main import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main() or 0)
