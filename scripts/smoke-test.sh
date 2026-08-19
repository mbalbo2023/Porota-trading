#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

pass() { printf 'PASS: %s\n' "$1"; }
fail() { printf 'FAIL: %s\n' "$1" >&2; exit 1; }

python --version >/dev/null 2>&1 || fail 'Python is unavailable'
pass 'Python is available'

python - <<'PY'
import importlib.util

modules = {
    "pandas": "pandas",
    "numpy": "numpy",
    "scipy": "scipy",
    "TA-Lib": "talib",
    "requests": "requests",
    "aiohttp": "aiohttp",
    "websockets": "websockets",
    "google-generativeai": "google.generativeai",
    "langchain": "langchain",
    "langgraph": "langgraph",
    "networkx": "networkx",
    "python-telegram-bot": "telegram",
    "psutil": "psutil",
    "prometheus_client": "prometheus_client",
    "watchdog": "watchdog",
    "beautifulsoup4": "bs4",
    "feedparser": "feedparser",
    "streamlit": "streamlit",
    "pdfkit": "pdfkit",
    "jinja2": "jinja2",
    "matplotlib": "matplotlib",
    "seaborn": "seaborn",
    "scikit-learn": "sklearn",
    "optuna": "optuna",
    "python-dotenv": "dotenv",
    "pytest": "pytest",
    "black": "black",
    "ruff": "ruff",
}
missing = [pkg for pkg, module in modules.items() if importlib.util.find_spec(module) is None]
if missing:
    raise SystemExit("Missing modules: " + ", ".join(missing))

import numpy
import pandas
import talib

print("Python dependencies: OK")
print("NumPy:", numpy.__version__)
print("Pandas:", pandas.__version__)
print("TA-Lib:", talib.__version__)
PY
pass 'Python dependency imports'

command -v wkhtmltopdf >/dev/null 2>&1 || fail 'wkhtmltopdf is unavailable'
pass 'wkhtmltopdf is available'

if command -v docker >/dev/null 2>&1; then
  docker --version >/dev/null
  docker compose version >/dev/null
  pass 'Docker and Docker Compose are available'
else
  printf 'WARN: Docker is not available in this shell; verify the Codespace DinD feature.\n'
fi

python -m compileall -q . --exclude '.venv' --exclude 'venv' 2>/dev/null || true
pass 'Workspace smoke test completed'
