"""Identidad reproducible de POROTA 17.0.0-rc5 para PRODUCTION_PAPER."""
VERSION = "17.0.0-rc5"
IMAGE = f"porota-trading-bot:{VERSION}"

# HF6 historical audited base retained for traceability.
BASE_AUDITED_COMMIT = "60cfb13e03f3bfe6dbfcbe3158dca9273279fb7e"
# Exact frozen source used by the validated HF6-v2 candidate1 release builder.
RELEASE_SOURCE_COMMIT = "a03cf47d5db01d8990a99f3f7836879c0606771b"
# SHA-256 of the exact operative-audit ZIP used as the RC4 engineering baseline.
OPERATIVE_BASE_SHA256 = "8e8c7a2596e6575c5e680d6e5c01a79ebb3ac1e89a26f11f7ba5bb4e1cd38499"
# RC4 test-ready1 exact deployed base for this hotfix lineage.
RC4_HF1_BASE_COMMIT = "75c9ddf15e3b19dd4839df8cce8bb72b51a2c629"
# Exact HF1 commit from which the HF2 consolidation was cut.
RC4_HF2_BASE_COMMIT = "3e60501c4031b1c525e7d7515ed166c6712ae23d"
# Exact integration commit from which the immutable RC5 release branch was cut.
RC5_INTEGRATION_BASE_COMMIT = "109d328f5a40c3887eb2c96310a65b02e0acc264"

MODE = "PRODUCTION_PAPER"
EXECUTION = "SIMULATED"
REAL_ORDER_CAPABILITY = "BLOCKED"
