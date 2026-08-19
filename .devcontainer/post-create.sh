#!/usr/bin/env bash
set -euo pipefail

cd /workspaces/Porota-trading

python --version
python -m pip --version

# Keep the workspace ready for source code that will be added later.
if [ -f requirements.txt ]; then
  python -m pip install --no-cache-dir -r requirements.txt
fi

# Verify the native tools required by the bot.
if command -v docker >/dev/null 2>&1; then
  docker --version
  docker compose version
fi

if command -v wkhtmltopdf >/dev/null 2>&1; then
  wkhtmltopdf --version
fi

python - <<'PY'
import importlib.util

required = [
    "pandas", "numpy", "scipy", "talib", "requests", "aiohttp",
    "websockets", "google.generativeai", "langchain", "langgraph",
    "networkx", "telegram", "psutil", "prometheus_client", "watchdog",
    "bs4", "feedparser", "streamlit", "pdfkit", "jinja2", "matplotlib",
    "seaborn", "sklearn", "optuna", "dotenv", "pytest", "black", "ruff",
]
missing = [name for name in required if importlib.util.find_spec(name) is None]
if missing:
    raise SystemExit("Missing Python modules: " + ", ".join(missing))

import talib
print("TA-Lib Python wrapper:", talib.__version__)
print("Porota Trading development environment: dependencies OK")
PY
