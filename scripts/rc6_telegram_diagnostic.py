#!/usr/bin/env python3
"""Read-only Telegram delivery diagnostic for the RC6 Droplet."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
import subprocess

DATA = Path("/opt/porota-trading/data")


def read_json(name):
    try:
        return json.loads((DATA / name).read_text(encoding="utf-8"))
    except Exception as exc:
        return {"status": "UNAVAILABLE", "error": type(exc).__name__}


def main() -> int:
    mode = read_json("operation_mode.json")
    delivery = read_json("telegram_last_delivery.json")
    sent_at = delivery.get("sent_at")
    age = None
    if sent_at:
        try:
            observed = datetime.fromisoformat(str(sent_at).replace("Z", "+00:00"))
            if observed.tzinfo is None:
                observed = observed.replace(tzinfo=timezone.utc)
            age = round((datetime.now(timezone.utc) - observed.astimezone(timezone.utc)).total_seconds(), 1)
        except (TypeError, ValueError):
            pass
    print("TELEGRAM_MODE_NOTIFICATION=" + str(mode.get("telegram_notification", "MISSING")))
    print("TELEGRAM_LAST_DELIVERY_STATUS=" + str(delivery.get("status", "MISSING")))
    print("TELEGRAM_LAST_DELIVERY_MESSAGE_ID=" + str(delivery.get("message_id", "MISSING")))
    print("TELEGRAM_LAST_DELIVERY_CHAT_VERIFIED=" + str(delivery.get("chat_verified", "MISSING")))
    print("TELEGRAM_LAST_DELIVERY_AGE_SECONDS=" + str(age if age is not None else "UNKNOWN"))
    units = subprocess.run(
        ["systemctl", "list-units", "--all", "--no-legend", "--plain"],
        capture_output=True, text=True, check=False,
    ).stdout.splitlines()
    matching = [line.split()[0] for line in units if "telegram" in line.lower() or "critical-approval" in line.lower()]
    print("TELEGRAM_RELATED_UNITS=" + ",".join(matching) if matching else "TELEGRAM_RELATED_UNITS=NONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
