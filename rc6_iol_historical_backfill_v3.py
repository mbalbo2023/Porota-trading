#!/usr/bin/env python3
"""RC6 IOL backfill V3: V2 engine + official /token authentication client."""
import rc6_iol_historical_backfill_v2 as engine
from rc6_iol_client_v2 import IOLClient

engine.IOLClient = IOLClient

if __name__ == "__main__":
    raise SystemExit(engine.main())
