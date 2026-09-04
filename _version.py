"""Identidad reproducible de POROTA RC4 candidate para testing PAPER."""
VERSION = "17.0.0-rc4-test-ready1"
IMAGE = f"porota-trading-bot:{VERSION}"

# HF6 historical audited base retained for traceability.
BASE_AUDITED_COMMIT = "60cfb13e03f3bfe6dbfcbe3158dca9273279fb7e"
# Exact frozen source used by the validated HF6-v2 candidate1 release builder.
RELEASE_SOURCE_COMMIT = "a03cf47d5db01d8990a99f3f7836879c0606771b"
# SHA-256 of the exact operative-audit ZIP used as the RC4 engineering baseline.
OPERATIVE_BASE_SHA256 = "8e8c7a2596e6575c5e680d6e5c01a79ebb3ac1e89a26f11f7ba5bb4e1cd38499"

MODE = "PRODUCTION_PAPER"
EXECUTION = "SIMULATED"
REAL_ORDER_CAPABILITY = "BLOCKED"
