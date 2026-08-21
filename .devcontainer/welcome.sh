#!/usr/bin/env bash
cat <<'BANNER'

===============================================================
 POROTA TRADING v16.1 — GITHUB CODESPACES
===============================================================
 This Codespace is for TESTING only.
 It does NOT start the trading bot automatically.

 Before running the stack:
   1) Confirm the final application package is present.
   2) Confirm .env is SANDBOX and contains no production secrets.
   3) Run: ./codespace-test.sh

 To stop the stack:
   docker compose down

 To stop the Codespace:
   Codespaces -> Stop codespace

 IMPORTANT:
 - Do not commit .env, credentials, tokens or runtime data.
 - A Codespace is a development environment, not production.
 - Production remains a separate Docker Compose deployment.
===============================================================

BANNER
