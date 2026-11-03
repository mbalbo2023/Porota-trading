"""Generation liveness budget; never changes quote or entry freshness gates."""
import math

POLL_SECONDS = 30
SOURCE_MAX_AGE_SECONDS = 120  # Existing runtime useful-observation ceiling.


def generation_budget(status):
    timing = status.get('generation_timing') if isinstance(status,dict) else None
    if not isinstance(timing,dict):
        return POLL_SECONDS, 'CADENCE_UNVERIFIED_LEGACY_CUT'
    elapsed = timing.get('tick_seconds')
    if (timing.get('poll_seconds') != POLL_SECONDS or type(elapsed) not in (int,float)
            or not math.isfinite(elapsed) or elapsed < 0):
        return POLL_SECONDS, 'CADENCE_MEASUREMENT_INVALID'
    # A cut spans compute + sleep. Two cycles detect absent progress; the
    # source ceiling prevents a slow tick granting unbounded apparent freshness.
    return min(SOURCE_MAX_AGE_SECONDS,2*(POLL_SECONDS+elapsed)), 'TWO_MEASURED_CYCLES_SOURCE_CAPPED'
