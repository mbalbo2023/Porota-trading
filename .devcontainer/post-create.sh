#!/usr/bin/env bash
set -euo pipefail

echo "== Porota Trading v16.1 / Codespaces bootstrap =="

if [ -f requirements.txt ]; then
  python -m pip install --upgrade pip
  python -m pip install -r requirements.txt
else
  echo "INFO: requirements.txt todavía no está presente."
  echo "El entorno queda preparado para recibir el paquete productivo."
fi

# Never create credentials. The committed template must contain placeholders only.
if [ ! -f .env ] && [ -f .env.example ]; then
  cp .env.example .env
  echo "Created .env from .env.example."
elif [ ! -f .env ]; then
  cat > .env <<'EOF'
ENVIRONMENT=SANDBOX
DASHBOARD_HOST=127.0.0.1
DASHBOARD_PORT=8000
STARTUP_REQUIRE_AUTH=true
ORDER_EXECUTION_MODE=confirm
EOF
  echo "Created a minimal SANDBOX .env because .env.example is not present yet."
fi

mkdir -p data data/logs data/proposals data/backups sre_vector_db

if find . -maxdepth 1 -name '*.py' -print -quit | grep -q .; then
  python -m compileall -q .
  echo "Python syntax check: OK"
else
  echo "INFO: application modules are not installed yet; syntax check deferred."
fi

echo
echo "Environment prepared. The trading stack is intentionally NOT started."
echo "Run ./codespace-test.sh after the final application package is present."
