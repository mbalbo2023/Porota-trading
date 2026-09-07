"""Secret-file entrypoint for the RC6 critical approval control plane.

Secrets are mounted read-only and copied into the process environment only after
container startup, so Docker inspect never contains Telegram credential values.
The trading observer's TELEGRAM_* variables are intentionally unsupported.
"""
from __future__ import annotations

import os
from pathlib import Path


def _read_secret(path: str, label: str) -> str:
    p = Path(path)
    if not p.is_file():
        raise RuntimeError(f"{label}_FILE_MISSING")
    value = p.read_text(encoding="utf-8").strip()
    if not value:
        raise RuntimeError(f"{label}_EMPTY")
    return value


def main() -> None:
    if os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_CHAT_ID"):
        raise RuntimeError("OBSERVER_TELEGRAM_CREDENTIAL_PRESENT_IN_CONTROL_PLANE")

    token_path = os.getenv(
        "POROTA_CRITICAL_TELEGRAM_TOKEN_FILE",
        "/run/secrets/critical_telegram.token",
    )
    chat_path = os.getenv(
        "POROTA_CRITICAL_TELEGRAM_CHAT_FILE",
        "/run/secrets/critical_telegram.chat",
    )
    os.environ["POROTA_CRITICAL_TELEGRAM_BOT_TOKEN"] = _read_secret(
        token_path, "CRITICAL_TELEGRAM_TOKEN"
    )
    os.environ["POROTA_CRITICAL_TELEGRAM_CHAT_ID"] = _read_secret(
        chat_path, "CRITICAL_TELEGRAM_CHAT"
    )

    from fg_critical_approval_gateway_rc6 import run

    run()


if __name__ == "__main__":
    main()
