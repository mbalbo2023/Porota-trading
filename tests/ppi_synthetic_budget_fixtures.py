"""Test-only, in-memory PPI transport lease for fake-wire fixtures.

This helper is intentionally not referenced by runtime code. Tests must opt in
explicitly before constructing a ProductionMarketReader: production continues
to reject Current/Book/Intraday when its signed shared budget is unavailable.
"""

from __future__ import annotations


class SyntheticWireLease:
    """Allow only synthetic, monkeypatched HTTP reads in fixture-owned tests."""

    def __init__(self):
        self.requests = []
        self._sequence = 0

    def acquire(self, endpoint, *, consumer=None, priority=None, **kwargs):
        if endpoint not in {"current", "book", "intraday"}:
            raise AssertionError(f"UNREVIEWED_TEST_ENDPOINT:{endpoint}")
        self._sequence += 1
        lease = f"SYNTHETIC_TEST_ONLY_{self._sequence}"
        self.requests.append((endpoint, consumer, priority, lease))
        return {"allowed": True, "lease": lease}

    def start(self, lease):
        if not any(row[3] == lease for row in self.requests):
            raise AssertionError("UNKNOWN_SYNTHETIC_TEST_LEASE")

    def finish(self, lease, *, status_code=None, error_code=None):
        self.start(lease)
