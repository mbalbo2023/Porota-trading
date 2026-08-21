#!/usr/bin/env bash
set -euo pipefail

echo "== Porota Trading v16.1 / Codespaces smoke test =="

command -v docker >/dev/null
docker compose version >/dev/null

test -f .env
grep -q '^ENVIRONMENT=SANDBOX' .env

if [ -f requirements.txt ]; then
  python -m compileall -q .
  python -m pytest -q
else
  echo "INFO: application package not present yet; test execution is deferred."
  exit 0
fi

docker compose config >/dev/null
docker compose build
docker compose up -d

cleanup() { docker compose down -v; }
trap cleanup EXIT

for i in $(seq 1 40); do
  if curl -fsS http://localhost:8000/health >/dev/null; then
    echo "GREEN: dashboard health OK"
    exit 0
  fi
  sleep 3
done

docker compose logs --tail=150
echo "RED: dashboard did not become healthy"
exit 1
