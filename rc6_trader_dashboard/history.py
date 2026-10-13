"""Bounded SQL view of immutable historical versions at an explicit cut.

The producer owns source authority and revisions. Window selection preserves
the earlier known version when the mutable canonical row has a later correction.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

KEY = ("symbol", "instrument_type", "market", "currency", "settlement", "date", "price_basis", "adjustment_basis")
VERSION_FIELDS = (*KEY, "open", "high", "low", "close", "volume", "source", "adjusted",
                  "provider_at", "observed_at", "version_known_at", "id")
REQUIRED_VERSION_FIELDS = set(VERSION_FIELDS)
ART = ZoneInfo("America/Argentina/Buenos_Aires")


def causal_history_sql(connection, cutoff, filters):
    """Return a named-column CTE plus parameters; all scope is in SQL."""
    from cu_history_store_v2_hf6 import source_rank
    if not isinstance(cutoff, datetime) or cutoff.tzinfo is None:
        raise ValueError("HISTORY_AWARE_CUT_REQUIRED")
    connection.create_function("rc6_history_source_rank", 1, source_rank, deterministic=True)
    where = ["rc6_instant_us(version_known_at)<=rc6_instant_us(?)", "date<=?"]
    args = [cutoff.isoformat(), cutoff.astimezone(ART).date().isoformat()]
    for name, column in (("family", "instrument_type"), ("currency", "currency"), ("market", "market"),
                         ("settlement", "settlement"), ("symbol", "symbol"), ("price_basis", "price_basis"),
                         ("adjustment_basis", "adjustment_basis")):
        if filters.get(name):
            where.append(f"{column}=? COLLATE NOCASE")
            args.append(filters[name])
    if filters.get("q"):
        where.append("symbol LIKE ?")
        args.append("%" + str(filters["q"]) + "%")
    fields = ",".join(VERSION_FIELDS)
    key = ",".join(KEY)
    cte = f"""WITH latest_source AS (
        SELECT {fields},ROW_NUMBER() OVER (
          PARTITION BY {key},source ORDER BY version_known_at DESC,id DESC) source_order
        FROM history_versions_v2 WHERE {' AND '.join(where)}),
      authority AS (
        SELECT {fields},ROW_NUMBER() OVER (
          PARTITION BY {key} ORDER BY rc6_history_source_rank(source),version_known_at DESC,source,id DESC) authority_order
        FROM latest_source WHERE source_order=1),
      causal_history AS (SELECT {fields} FROM authority WHERE authority_order=1)
    """
    return cte, args
