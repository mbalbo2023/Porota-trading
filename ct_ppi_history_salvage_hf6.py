"""HF6-v2: salva filas PPI históricas válidas sin aceptar filas defectuosas.

Las velas FULL_OHLC válidas se escriben en History Store v2. Desde la candidata
RC4-HF2, las filas rechazadas por FULL_OHLC pueden conservar sólo fecha+close
en una serie paralela explícita, sin reparar ni sintetizar open/high/low/volumen.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
import json
from hashlib import sha256
from bs_instrument_contracts import cash_currency

import cp_history_ingest_policy_hf6 as policy
import cq_history_attempt_ledger_hf6 as ledger
import cu_history_store_v2_hf6 as history_v2
import ea_history_close_series_hf2 as close_series
from cv_history_store_adapter_hf6 import default_history_store


def _date_bound(value):
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return history_v2.instant(value).astimezone(history_v2.ART).date()
    if isinstance(value, date):
        return value
    try:
        if len(str(value)) == 10:
            return date.fromisoformat(str(value))
        return history_v2.instant(value).astimezone(history_v2.ART).date()
    except (TypeError, ValueError) as exc:
        raise ValueError("HISTORY_REQUEST_BOUND_INVALID") from exc


def _as_of(value) -> datetime:
    if value in (None, ""):
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise ValueError("HISTORY_ATTEMPTED_AT_INVALID") from exc
    return history_v2.instant(parsed)


def _init_rejections(store) -> None:
    with store.connect() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS history_row_rejections_v2(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          attempt_id INTEGER NOT NULL,
          symbol TEXT NOT NULL,
          instrument_type TEXT NOT NULL,
          market TEXT NOT NULL,
          currency TEXT NOT NULL,
          settlement TEXT NOT NULL,
          row_index INTEGER NOT NULL,
          reason TEXT NOT NULL,
          recorded_at TEXT NOT NULL
        )""")
        columns={row[1] for row in c.execute('PRAGMA table_info(history_row_rejections_v2)')}
        if 'currency' not in columns:
            c.execute('ALTER TABLE history_row_rejections_v2 ADD COLUMN currency TEXT')
        c.execute("""CREATE INDEX IF NOT EXISTS idx_history_rejections_v2_identity
          ON history_row_rejections_v2(symbol,instrument_type,market,settlement,attempt_id)""")


def _v2_candles(*, symbol, instrument_type, market,currency, settlement,
                rows, observed_at):
    return [
        history_v2.Candle(
            symbol=str(symbol).upper(),
            instrument_type=str(instrument_type).upper(),
            market=str(market).upper(),
            currency=currency,
            price_basis="RAW",
            settlement=str(settlement).upper(),
            date=policy._dt(row["date"]).astimezone(history_v2.ART).date().isoformat(),
            open=float(row["openingPrice"]),
            high=float(row["max"]),
            low=float(row["min"]),
            close=float(row["price"]),
            volume=float(row["volume"]),
            source="PPI_PRODUCTION_HISTORY",
            adjusted=False,
            observed_at=observed_at,
            provider_at=policy._dt(row['date']).isoformat(),
            metadata={"ready_paper_implication":"NONE","history_quality":"FULL_OHLC"},
        )
        for row in rows
    ]


def _validate_payload(payload, *, symbol, instrument_type, market, currency,
                      settlement, attempted_dt, attempted_iso, requested_from_date,
                      requested_to_date):
    """Preserve original row indices and quarantine every conflicting day."""
    rejected = []
    days = {}
    for index, row in enumerate(payload):
        checked = policy.validate_provider_history([row], as_of=attempted_dt,
            date_from=requested_from_date, date_to=requested_to_date)
        if not checked.valid_rows:
            rejected.append(policy.RejectedRow(index, checked.rejected_rows[0].reason))
            continue
        try:
            candidate = _v2_candles(symbol=symbol, instrument_type=instrument_type,
                market=market, currency=currency, settlement=settlement,
                rows=checked.valid_rows, observed_at=attempted_iso)[0].normalized()
            candidate.validate()
            if row.get('currency') and cash_currency(row['currency']) != currency:
                raise ValueError('HISTORY_CURRENCY_CONFLICT')
            signature = (candidate.open, candidate.high, candidate.low,
                         candidate.close, candidate.volume)
            days.setdefault(candidate.date, []).append((index, checked.valid_rows[0], signature))
        except (ValueError, TypeError, OverflowError) as exc:
            reason = str(exc) if str(exc).startswith('HISTORY_') else 'HISTORY_ROW_INVALID'
            rejected.append(policy.RejectedRow(index, reason))
    accepted = []
    for day in sorted(days):
        entries = days[day]
        if len({entry[2] for entry in entries}) > 1:
            rejected.extend(policy.RejectedRow(entry[0], 'HISTORY_DUPLICATE_DAY_CONFLICT')
                            for entry in entries)
            continue
        accepted.append(entries[0][1])
        rejected.extend(policy.RejectedRow(entry[0], 'HISTORY_DUPLICATE_DAY')
                        for entry in entries[1:])
    rejected.sort(key=lambda item: item.index)
    return policy.ValidatedHistory(tuple(accepted), tuple(rejected), len(payload),
        accepted[0]['date'] if accepted else None, accepted[-1]['date'] if accepted else None)


def _ingest_ppi_payload(store, *, symbol: str, instrument_type: str,
                       market: str, settlement: str, payload,
                       requested_from=None, requested_to=None,
                       attempted_at=None, history_store=None,currency=None,ingest_key=None) -> dict:
    """Persist full OHLC and, separately, safe close-only evidence.

    ``store`` is observer evidence/readiness storage. ``history_store`` defaults
    to the dedicated historical DB. No historical row grants trading readiness.
    A close-only row never enters ``history_canonical_v2`` and therefore cannot
    degrade or replace a FULL_OHLC canonical candle.
    """
    attempted_dt = _as_of(attempted_at)
    attempted_iso = history_v2.utc(attempted_dt)
    requested_from_date = _date_bound(requested_from)
    requested_to_date = _date_bound(requested_to)
    market = str(market or "").strip().upper()
    if not market or market == "UNKNOWN":
        raise ValueError("HISTORY_MARKET_UNVERIFIED")

    result = _validate_payload(payload, symbol=symbol, instrument_type=instrument_type,
        market=market, currency=currency, settlement=settlement, attempted_dt=attempted_dt,
        attempted_iso=attempted_iso, requested_from_date=requested_from_date,
        requested_to_date=requested_to_date)

    hstore = history_store or default_history_store()
    stored = history_v2.append_many(
        hstore,
        _v2_candles(
            symbol=symbol,
            instrument_type=instrument_type,
            market=market,
            currency=currency,
            settlement=settlement,
            rows=result.valid_rows,
            observed_at=attempted_iso,
        ),attempt_key=ingest_key+':FULL',
    ) if result.valid_rows else {
        "versions_appended":0,"canonical_updates":0,"protected_by_precedence":0
    }
    _step(hstore,ingest_key,'FULL_COMMITTED',stored)

    close_rows = close_series.extract_rejected_close_evidence(
        payload,
        result.rejected_rows,
        symbol=symbol,
        instrument_type=instrument_type,
        market=market,
        currency=currency,
        settlement=settlement,
        observed_at=attempted_iso,
        as_of=attempted_dt,
        date_from=requested_from_date,
        date_to=requested_to_date,
    )
    close_stored = close_series.append_many(hstore, close_rows) if close_rows else {
        "versions_appended":0,"canonical_updates":0
    }
    _step(hstore,ingest_key,'CLOSE_COMMITTED',close_stored)
    close_first = min((row.date for row in close_rows), default=None)
    close_last = max((row.date for row in close_rows), default=None)

    ledger.init_schema(store)
    _init_rejections(store)
    attempt = ledger.HistoryAttempt(
        symbol=str(symbol).upper(),
        instrument_type=str(instrument_type).upper(),
        settlement=str(settlement).upper(),
        source="PPI_PRODUCTION_HISTORY",
        requested_from=requested_from_date.isoformat() if requested_from_date else None,
        requested_to=requested_to_date.isoformat() if requested_to_date else None,
        attempted_at=attempted_iso,
        completed_at=datetime.now(timezone.utc).isoformat(),
        provider_rows=result.provider_rows,
        valid_rows=result.valid_count,
        rejected_rows=result.rejected_count,
        state=result.storage_quality,
        detail=(f"market={market}; context={result.context_state}; "
                f"ratio={result.valid_ratio:.6f}; close_only={len(close_rows)}; "
                "sin interpolacion ni velas sinteticas"),
        raw_body_hash=ledger.body_hash(payload),
        metadata={
            "market": market,
            "currency": currency,
            "identity":[str(symbol).upper(),str(instrument_type).upper(),market,currency,str(settlement).upper()],
            "price_basis":"RAW",
            "ingest_key":ingest_key,
            "batch_semantics":"BOUNDED_ATOMIC_STEPS_WITH_DURABLE_RETRY",
            "context_state": result.context_state,
            "valid_ratio": result.valid_ratio,
            "first_date": result.first_date,
            "last_date": result.last_date,
            "full_ohlc_rows": result.valid_count,
            "close_only_rows": len(close_rows),
            "close_only_first_date": close_first,
            "close_only_last_date": close_last,
            "close_only_quality": close_series.QUALITY,
            "close_only_store": "history_close_canonical_v1",
            "ready_paper_implication": "NONE",
            "execution_price_implication": "NONE",
            "history_store": "history_canonical_v2",
        },
    )
    attempt_id = _append_attempt_once(store, attempt,ingest_key)

    if result.rejected_rows:
        with store.connect() as c:
            c.executemany(
                """INSERT INTO history_row_rejections_v2(
                  attempt_id,symbol,instrument_type,market,currency,settlement,row_index,reason,recorded_at)
                  SELECT ?,?,?,?,?,?,?,?,? WHERE NOT EXISTS(
                    SELECT 1 FROM history_row_rejections_v2 WHERE attempt_id=? AND row_index=? AND reason=?)""",
                [
                    (attempt_id, str(symbol).upper(), str(instrument_type).upper(), market,currency,
                     str(settlement).upper(), int(item.index), str(item.reason), attempted_iso,
                     attempt_id,int(item.index),str(item.reason))
                    for item in result.rejected_rows
                ],
            )

    return {
        "attempt_id": attempt_id,
        "ingest_key":ingest_key,"identity":[symbol,instrument_type,market,currency,settlement],
        "batch_semantics":"BOUNDED_ATOMIC_STEPS_WITH_DURABLE_RETRY",
        "provider_rows": result.provider_rows,
        "valid_rows": result.valid_count,
        "full_ohlc_rows": result.valid_count,
        "rejected_rows": result.rejected_count,
        "close_only_rows": len(close_rows),
        "close_only_versions_appended": close_stored["versions_appended"],
        "close_only_canonical_updates": close_stored["canonical_updates"],
        "versions_appended": stored["versions_appended"],
        "canonical_updates": stored["canonical_updates"],
        "protected_by_precedence": stored["protected_by_precedence"],
        "valid_ratio": result.valid_ratio,
        "storage_quality": result.storage_quality,
        "context_state": result.context_state,
        "first_date": result.first_date,
        "last_date": result.last_date,
        "close_only_first_date": close_first,
        "close_only_last_date": close_last,
        "ready_paper_implication": "NONE",
        "execution_price_implication": "NONE",
    }


def _step(store,key,state,result):
    # A history-DB saga is authoritative even if the observer ledger write
    # fails later. Retrying identical input deduplicates both atomic stages.
    counts={name:value for name,value in result.items() if name!='row_results'}
    with store.connect() as c:
        c.execute('UPDATE history_ingest_sagas_v2 SET state=?,last_step_json=? WHERE ingest_key=?',
                  (state,json.dumps(counts,sort_keys=True),key))


def _append_attempt_once(store,attempt,key):
    ledger.init_schema(store)
    fields=('symbol','instrument_type','settlement','source','requested_from','requested_to',
            'attempted_at','completed_at','provider_rows','valid_rows','rejected_rows','state',
            'error_class','detail','raw_body_hash','metadata_json')
    with store.connect() as c:
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_history_attempt_ingest_key_v2 ON history_attempt_ledger_v2(json_extract(metadata_json,'$.ingest_key'))")
        c.execute('BEGIN IMMEDIATE')
        try:
            row=c.execute("SELECT id FROM history_attempt_ledger_v2 WHERE json_extract(metadata_json,'$.ingest_key')=?",(key,)).fetchone()
            if row: result=int(row[0])
            else:
                values=tuple(json.dumps(attempt.metadata,sort_keys=True,separators=(',',':')) if name=='metadata_json' else getattr(attempt,name) for name in fields)
                result=int(c.execute('INSERT INTO history_attempt_ledger_v2('+','.join(fields)+') VALUES('+','.join('?' for _ in fields)+')',values).lastrowid)
            c.commit();return result
        except BaseException:
            c.rollback();raise


def ingest_ppi_payload(store, *, symbol, instrument_type,market,settlement,payload,
                       currency=None,requested_from=None,requested_to=None,
                       attempted_at=None,history_store=None):
    currency=cash_currency(currency)
    if not isinstance(payload,list):
        raise ValueError('HISTORY_PAYLOAD_NOT_LIST')
    if len(payload)>history_v2.MAX_BATCH_ROWS:
        raise ValueError('HISTORY_BATCH_ROW_BUDGET_EXHAUSTED')
    attempted_dt=_as_of(attempted_at)
    if attempted_dt>datetime.now(timezone.utc):
        raise ValueError('HISTORY_KNOWN_AT_IN_FUTURE')
    identity=[str(x).strip().upper() for x in (symbol,instrument_type,market,currency,settlement)]
    if any(x in {'','UNKNOWN','NONE'} for x in identity):
        raise ValueError('HISTORY_IDENTITY_UNVERIFIED')
    if identity[1] not in policy.OPERATIONAL_HISTORY_FAMILIES | policy.LEGACY_HISTORY_FAMILIES:
        raise ValueError('HISTORY_FAMILY_UNVERIFIED')
    bounds=[_date_bound(x).isoformat() if _date_bound(x) is not None else None for x in (requested_from,requested_to)]
    if all(bounds) and bounds[0]>bounds[1]:
        raise ValueError('HISTORY_REQUEST_BOUNDS_REVERSED')
    key=sha256(json.dumps([identity,bounds,ledger.body_hash(payload)],
                         sort_keys=True,default=str,separators=(',',':')).encode()).hexdigest()
    hstore=history_store or default_history_store()
    history_v2.init_schema(hstore)
    with hstore.connect() as c:
        c.execute('''CREATE TABLE IF NOT EXISTS history_ingest_sagas_v2(
          ingest_key TEXT PRIMARY KEY,identity_json TEXT NOT NULL,payload_hash TEXT NOT NULL,
          state TEXT NOT NULL,first_attempted_at TEXT NOT NULL,last_step_json TEXT NOT NULL,
          last_attempted_at TEXT NOT NULL,attempt_count INTEGER NOT NULL)''')
        columns={row[1] for row in c.execute('PRAGMA table_info(history_ingest_sagas_v2)')}
        if 'last_attempted_at' not in columns:c.execute("ALTER TABLE history_ingest_sagas_v2 ADD COLUMN last_attempted_at TEXT NOT NULL DEFAULT ''")
        if 'attempt_count' not in columns:c.execute('ALTER TABLE history_ingest_sagas_v2 ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0')
        c.execute("""INSERT INTO history_ingest_sagas_v2(ingest_key,identity_json,payload_hash,
          state,first_attempted_at,last_step_json,last_attempted_at,attempt_count)
          VALUES(?,?,?,'STARTED',?,'{}',?,1) ON CONFLICT(ingest_key) DO UPDATE SET
          last_attempted_at=MAX(last_attempted_at,excluded.last_attempted_at),attempt_count=attempt_count+1""",
                  (key,json.dumps(identity),ledger.body_hash(payload),history_v2.utc(attempted_dt),history_v2.utc(attempted_dt)))
        first_at=c.execute('SELECT first_attempted_at FROM history_ingest_sagas_v2 WHERE ingest_key=?',
                           (key,)).fetchone()[0]
    # A retry resumes evidence first observed by this saga. Its availability
    # never shifts to the retry wall clock after an intermediate commit.
    result=_ingest_ppi_payload(store,symbol=identity[0],instrument_type=identity[1],market=identity[2],
        currency=currency,settlement=identity[4],payload=payload,requested_from=requested_from,
        requested_to=requested_to,attempted_at=first_at,history_store=hstore,ingest_key=key)
    _step(hstore,key,'OBSERVER_COMMITTED',result)
    return result
