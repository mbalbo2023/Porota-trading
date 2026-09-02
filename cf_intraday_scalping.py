"""Colector intradiario y scanner de scalping PAPER v17 HF3.

Usa exclusivamente ``MarketData/Intraday`` mediante la fachada PPI de solo
lectura. No expone métodos de órdenes ni convierte datos incompletos en fills.
El volumen se habilita automáticamente sólo después de observar continuidad
temporal, solapamiento estable y nuevos minutos durante una rueda abierta.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, time as wall_time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from bs_instrument_contracts import aware_datetime, family_name


TZ = ZoneInfo("America/Argentina/Buenos_Aires")
FOCUS = ("GGAL", "YPFD", "PAMP", "BMA", "BBAR", "SUPV", "CEPU", "AAPL")
SUPPORTED_FAMILIES = {
    "ACCIONES", "BONOS", "CEDEARS", "ETF", "ETFS", "LETRAS", "ON",
    "OBLIGACIONES", "OPCIONES", "FUTUROS", "CAUCIONES", "FCI",
}


def _stamp(value):
    return aware_datetime(value).astimezone(timezone.utc).isoformat(timespec="microseconds")


def _decimal(value, *, positive=False, nonnegative=False):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("INTRADAY_INVALID_NUMBER") from exc
    if not result.is_finite() or (positive and result <= 0) or (nonnegative and result < 0):
        raise ValueError("INTRADAY_NUMBER_OUT_OF_RANGE")
    return result


def _field(row, name):
    if not isinstance(row, dict):
        return None
    return {str(key).lower(): value for key, value in row.items()}.get(name.lower())


def normalize_payload(payload, *, received_at, local_day=None):
    """Valida la forma observada de PPI sin inventar OHLC ni unidad nominal."""
    if not isinstance(payload, list):
        raise ValueError("INTRADAY_NOT_A_LIST")
    received = aware_datetime(received_at).astimezone(timezone.utc)
    local_day = local_day or received.astimezone(TZ).date()
    points = []
    seen = set()
    for raw in payload:
        if not isinstance(raw, dict):
            raise ValueError("INTRADAY_ROW_NOT_OBJECT")
        event = aware_datetime(_field(raw, "date")).astimezone(timezone.utc)
        price = _decimal(_field(raw, "price"), positive=True)
        volume = _decimal(_field(raw, "volume"), nonnegative=True)
        if event > received + timedelta(seconds=5):
            raise ValueError("INTRADAY_EVENT_IN_FUTURE")
        if event.astimezone(TZ).date() != local_day:
            continue
        event_at = event.isoformat(timespec="microseconds")
        if event_at in seen:
            raise ValueError("INTRADAY_DUPLICATE_MINUTE")
        seen.add(event_at)
        points.append((event_at, price, volume))
    if points != sorted(points, key=lambda item: item[0]):
        raise ValueError("INTRADAY_NOT_ASCENDING")
    return points


def init_schema(store):
    with store.connect() as connection:
        connection.executescript("""
        CREATE TABLE IF NOT EXISTS ppi_intraday_points(
          symbol TEXT NOT NULL, asset_class TEXT NOT NULL, market TEXT NOT NULL,
          currency TEXT NOT NULL, settlement TEXT NOT NULL, event_at TEXT NOT NULL,
          price TEXT NOT NULL, volume TEXT NOT NULL, first_received_at TEXT NOT NULL,
          last_verified_at TEXT NOT NULL, source TEXT NOT NULL,
          PRIMARY KEY(symbol,asset_class,market,currency,settlement,event_at));
        CREATE INDEX IF NOT EXISTS idx_intraday_identity_time ON ppi_intraday_points(
          symbol,asset_class,market,currency,settlement,event_at);
        CREATE TABLE IF NOT EXISTS ppi_intraday_contract_state(
          symbol TEXT NOT NULL, asset_class TEXT NOT NULL, market TEXT NOT NULL,
          currency TEXT NOT NULL, settlement TEXT NOT NULL, state TEXT NOT NULL,
          observations INTEGER NOT NULL, stable_overlap INTEGER NOT NULL,
          changed_closed_points INTEGER NOT NULL, new_points INTEGER NOT NULL,
          last_source_at TEXT, checked_at TEXT NOT NULL, detail TEXT NOT NULL,
          PRIMARY KEY(symbol,asset_class,market,currency,settlement));
        CREATE TABLE IF NOT EXISTS scalping_candidates(
          id INTEGER PRIMARY KEY AUTOINCREMENT, evaluated_at TEXT NOT NULL,
          symbol TEXT NOT NULL, asset_class TEXT NOT NULL, market TEXT NOT NULL,
          currency TEXT NOT NULL, settlement TEXT NOT NULL, action TEXT NOT NULL,
          score TEXT NOT NULL, price TEXT, volume TEXT, points INTEGER NOT NULL,
          reason TEXT NOT NULL, economics_json TEXT NOT NULL,
          UNIQUE(symbol,asset_class,market,currency,settlement,evaluated_at));
        CREATE INDEX IF NOT EXISTS idx_scalping_candidates_at ON scalping_candidates(evaluated_at,id);
        CREATE TABLE IF NOT EXISTS intraday_scalping_worker_state(
          id INTEGER PRIMARY KEY CHECK(id=1), heartbeat_at TEXT NOT NULL,
          state TEXT NOT NULL, cursor INTEGER NOT NULL, selected INTEGER NOT NULL,
          successful INTEGER NOT NULL, failed INTEGER NOT NULL,
          points_inserted INTEGER NOT NULL, confirmed_identities INTEGER NOT NULL,
          candidates INTEGER NOT NULL, real_orders_sent INTEGER NOT NULL,
          detail TEXT NOT NULL);
        """)


def _identity(record):
    return tuple(record[key] for key in
                 ("ticker", "instrument_type", "market", "currency", "settlement"))


def _market_open(at):
    local = aware_datetime(at).astimezone(TZ)
    try:
        import ak_byma_calendar as calendar
        if not calendar.es_dia_habil_operativo(local.date()):
            return False
    except Exception:
        if local.weekday() >= 5:
            return False
    # El contrato observado comienza a las 10:30. El último punto aceptado es 17:00.
    return wall_time(10, 30) <= local.time().replace(tzinfo=None) <= wall_time(17, 0)


def select_batch(store, *, limit, cursor=0):
    """Foco + rotación completa. Elegible no equivale a ejecutable."""
    if not 8 <= limit <= 40:
        raise ValueError("INTRADAY_BATCH_LIMIT_OUT_OF_RANGE")
    with store.connect() as connection:
        rows = [dict(row) for row in connection.execute("""
          SELECT ticker,instrument_type,market,currency,settlement,capability,status
          FROM financial_instrument_catalog
          WHERE status='AVAILABLE' AND currency<>'UNKNOWN' AND market<>'UNKNOWN'
          ORDER BY instrument_type,market,currency,ticker,settlement
        """).fetchall()]
        opened = {row[0] for row in connection.execute(
            "SELECT DISTINCT symbol FROM paper_positions WHERE status='OPEN'").fetchall()}
    unique = {}
    for row in rows:
        try:
            family_name(row["instrument_type"])
        except ValueError:
            continue
        # Intraday no recibe moneda/mercado: no duplicar la misma consulta literal.
        unique.setdefault((row["ticker"], row["instrument_type"], row["settlement"]), row)
    rows = list(unique.values())
    priority = [row for row in rows if row["ticker"] in opened or row["ticker"] in FOCUS]
    priority_keys = {_identity(row) for row in priority}
    rotation = [row for row in rows if _identity(row) not in priority_keys]
    slots = max(0, limit - len(priority))
    if rotation and slots:
        start = cursor % len(rotation)
        selected = [rotation[(start + index) % len(rotation)] for index in range(min(slots, len(rotation)))]
        next_cursor = (start + len(selected)) % len(rotation)
    else:
        selected, next_cursor = [], cursor
    return (priority + selected)[:limit], next_cursor, len(rows)


def _state(store, identity):
    with store.connect() as connection:
        row = connection.execute("""SELECT * FROM ppi_intraday_contract_state
          WHERE symbol=? AND asset_class=? AND market=? AND currency=? AND settlement=?""",
          identity).fetchone()
    return dict(row) if row else None


def persist_payload(store, record, points, *, received_at):
    identity = _identity(record)
    cutoff = _stamp(aware_datetime(received_at) - timedelta(minutes=2))
    previous = _state(store, identity)
    stable = changed = inserted = 0
    down_steps = sum(1 for left, right in zip(points, points[1:]) if right[2] < left[2])
    with store.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        for event_at, price, volume in points:
            existing = connection.execute("""SELECT price,volume FROM ppi_intraday_points
              WHERE symbol=? AND asset_class=? AND market=? AND currency=? AND settlement=? AND event_at=?""",
              (*identity, event_at)).fetchone()
            values = (format(price, "f"), format(volume, "f"))
            if existing:
                if event_at < cutoff:
                    if tuple(existing) == values:
                        stable += 1
                    else:
                        changed += 1
                if tuple(existing) == values:
                    connection.execute("""UPDATE ppi_intraday_points SET last_verified_at=?
                      WHERE symbol=? AND asset_class=? AND market=? AND currency=? AND settlement=? AND event_at=?""",
                      (received_at, *identity, event_at))
                continue
            connection.execute("""INSERT INTO ppi_intraday_points VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
              (*identity, event_at, *values, received_at, received_at, "PPI_MARKETDATA_INTRADAY"))
            inserted += 1
        observations = (previous or {}).get("observations", 0) + 1
        prior_stable = (previous or {}).get("stable_overlap", 0)
        prior_changed = (previous or {}).get("changed_closed_points", 0)
        confirmed_now = (observations >= 2 and stable >= 5 and inserted >= 1
                         and down_steps >= 1 and changed == 0 and prior_changed == 0)
        state = ("CONFIRMED_INTERVAL_VOLUME" if confirmed_now or (
                    (previous or {}).get("state") == "CONFIRMED_INTERVAL_VOLUME"
                    and changed == 0 and prior_changed == 0)
                 else "REJECTED_MUTABLE_CLOSED_POINTS" if changed or prior_changed
                 else "PENDING_LIVE_CONFIRMATION")
        last_source = points[-1][0] if points else None
        detail = (f"observaciones={observations}; solapamiento_estable={stable}; "
                  f"cerrados_modificados={changed}; nuevos={inserted}; descensos_volumen={down_steps}")
        connection.execute("""INSERT OR REPLACE INTO ppi_intraday_contract_state
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
          (*identity, state, observations, max(prior_stable, stable), prior_changed + changed,
           inserted, last_source, received_at, detail))
    return {"state": state, "inserted": inserted, "stable": stable,
            "changed": changed, "down_steps": down_steps}


def evaluate_candidate(store, record, *, at):
    identity = _identity(record)
    contract = _state(store, identity) or {}
    reason = ""
    action = "HOLD"
    score = Decimal("0")
    economics = {"binding": True, "execution_enabled": False, "passed": False}
    with store.connect() as connection:
        points = connection.execute("""SELECT event_at,price,volume FROM ppi_intraday_points
          WHERE symbol=? AND asset_class=? AND market=? AND currency=? AND settlement=?
            AND julianday(event_at)>=julianday(?) AND julianday(event_at)<=julianday(?)
          ORDER BY event_at DESC LIMIT 30""",
          (*identity, _stamp(aware_datetime(at)-timedelta(minutes=45)), _stamp(at))).fetchall()
        quote = connection.execute("""SELECT * FROM market_snapshots
          WHERE symbol=? AND asset_class=? AND settlement=? AND currency=? AND market=?
          ORDER BY id DESC LIMIT 1""",
          (record["ticker"],record["instrument_type"],record["settlement"],
           record["currency"],record["market"])).fetchone()
    points = list(reversed(points))
    if record.get("capability") != "READY_PAPER_SPOT":
        reason = record.get("capability") or "CONTRACT_NOT_EXECUTABLE"
    elif contract.get("state") != "CONFIRMED_INTERVAL_VOLUME":
        reason = contract.get("state") or "PENDING_LIVE_CONFIRMATION"
    elif len(points) < 15:
        reason = "INSUFFICIENT_INTRADAY_POINTS"
    elif not quote:
        reason = "NO_CURRENT_BOOK"
    else:
        try:
            prices = [_decimal(row["price"], positive=True) for row in points]
            volumes = [_decimal(row["volume"], nonnegative=True) for row in points]
            bid, ask = _decimal(quote["bid"], positive=True), _decimal(quote["ask"], positive=True)
            if ask < bid:
                raise ValueError("CROSSED_BOOK")
            quote_age = (aware_datetime(at)-aware_datetime(quote["book_at"])).total_seconds()
            if not 0 <= quote_age <= 120:
                raise ValueError("STALE_BOOK")
            short = sum(prices[-3:]) / 3
            long = sum(prices[-15:]) / 15
            momentum = short / long - 1
            spread = ask / bid - 1
            observed_range = max(prices[-15:]) / min(prices[-15:]) - 1
            import au_fee_schedule
            one_leg = Decimal(str(au_fee_schedule.costo_por_tramo(family_name(record["instrument_type"]))))
            modeled_roundtrip = one_leg * 2 + spread + Decimal("0.0004")
            required_move = modeled_roundtrip + Decimal(os.getenv("PAPER_SCALPING_MIN_NET_MARGIN", "0.005"))
            score = max(Decimal(0), min(Decimal(1), Decimal("0.5") + momentum*40 - spread*10))
            economics.update({
                "modeled_roundtrip_fraction": str(modeled_roundtrip),
                "required_move_fraction": str(required_move),
                "observed_15m_range_fraction": str(observed_range),
                "spread_fraction": str(spread), "momentum": str(momentum),
            })
            if sum(volumes[-5:]) <= 0:
                reason = "NO_RECENT_VOLUME"
            elif spread > Decimal(os.getenv("PAPER_SCALPING_MAX_SPREAD", "0.005")):
                reason = "SPREAD_TOO_WIDE"
            elif observed_range <= required_move:
                reason = "ECONOMICS_BINDING_RANGE_BELOW_COST"
            elif score < Decimal(os.getenv("PAPER_SCALPING_SCORE_THRESHOLD", "0.68")):
                reason = "SCALPING_SCORE_BELOW_THRESHOLD"
            else:
                action, reason = "BUY_CANDIDATE", "VALIDATED_SCALPING_CANDIDATE"
                economics["passed"] = True
        except (ValueError, TypeError, InvalidOperation) as exc:
            reason = str(exc) or type(exc).__name__
    evaluated = _stamp(at)
    with store.connect() as connection:
        connection.execute("""INSERT OR IGNORE INTO scalping_candidates
          VALUES(NULL,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
          (evaluated,*identity,action,str(score),str(points[-1]["price"]) if points else None,
           str(points[-1]["volume"]) if points else None,len(points),reason,
           json.dumps(economics,sort_keys=True)))
    return action


def promote_paper_candidate(store, record, *, at):
    """Convertir un candidato validado en fill exclusivamente simulado.

    Comparte caja, límite global, supervisor, daily-risk y ledger con el motor
    principal. No importa ni invoca ningún cliente de órdenes PPI.
    """
    mode = os.getenv("PAPER_SCALPING_MODE", "ACTIVE_OBSERVE").upper()
    if mode != "ACTIVE_PAPER":
        return "OBSERVE_ONLY"
    from be_paper_engine import Quote
    from bv_paper_runtime import broker_from_environment
    with store.connect() as connection:
        scalp_open = 0
        for row in connection.execute("SELECT features_json FROM paper_positions WHERE status='OPEN'"):
            try:
                scalp_open += int(json.loads(row[0] or "{}").get("execution_style") == "SCALPING_PAPER")
            except (ValueError, TypeError):
                continue
        if scalp_open >= int(os.getenv("PAPER_SCALPING_MAX_OPEN_POSITIONS", "1")):
            return "SCALPING_POSITION_LIMIT"
        quote_row = connection.execute("""SELECT * FROM market_snapshots
          WHERE symbol=? AND asset_class=? AND settlement=? AND currency=? AND market=?
          ORDER BY id DESC LIMIT 1""",
          (record["ticker"],record["instrument_type"],record["settlement"],
           record["currency"],record["market"])).fetchone()
        candidate = connection.execute("""SELECT * FROM scalping_candidates
          WHERE symbol=? AND asset_class=? AND market=? AND currency=? AND settlement=?
          ORDER BY id DESC LIMIT 1""", _identity(record)).fetchone()
    if not quote_row or not candidate or candidate["action"] != "BUY_CANDIDATE":
        return "CANDIDATE_NOT_AVAILABLE"
    q = Quote(
        symbol=quote_row["symbol"], asset_class=quote_row["asset_class"],
        settlement=quote_row["settlement"], last=_decimal(quote_row["last"],nonnegative=True),
        bid=_decimal(quote_row["bid"],positive=True), ask=_decimal(quote_row["ask"],positive=True),
        bid_size=_decimal(quote_row["bid_size"],nonnegative=True),
        ask_size=_decimal(quote_row["ask_size"],nonnegative=True),
        observed_at=quote_row["observed_at"], currency=quote_row["currency"],
        market=quote_row["market"], metadata_source=quote_row["metadata_source"],
        opening_block_reason=quote_row["opening_block_reason"], book_at=quote_row["book_at"],
        trade_at=quote_row["trade_at"], last_kind=quote_row["last_kind"])
    economics = json.loads(candidate["economics_json"] or "{}")
    economics.update(passed=True, binding=True, execution_enabled=True,
                     model="SCALPING_INTRADAY_HF5")
    features = {
        "execution_style": "SCALPING_PAPER",
        "scalping_max_hold_minutes": int(os.getenv("PAPER_SCALPING_MAX_HOLD_MINUTES", "30")),
        "intraday_points": candidate["points"], "intraday_score": candidate["score"],
        "candidate_reason": candidate["reason"], "economics": economics,
    }
    key = "SCALPING:" + ":".join(_identity(record)) + ":" + str(candidate["evaluated_at"])
    if not store.record_decision(key, q, "BUY", Decimal(candidate["score"]),
                                 "Candidato scalping validado", features):
        return "DUPLICATE_DECISION"
    broker = broker_from_environment(
        store, risk_pct=os.getenv("PAPER_SCALPING_RISK_PER_TRADE", "0.001"),
        stop_loss_pct=os.getenv("PAPER_SCALPING_STOP_LOSS_PCT", "0.008"),
        target_gain_pct=os.getenv("PAPER_SCALPING_TARGET_GAIN_PCT", "0.02"))
    opened, reason, paper_id = broker._open(q, Decimal(candidate["score"]), features)
    store.record_gates(q, key, "APPROVE", "NOT_USED",
                       "APPROVE" if opened else "BLOCKED",
                       "OPENED_SIMULATED" if opened else "BLOCKED", reason,
                       paper_id=paper_id, detail=features)
    if opened:
        store.event("SCALPING_PAPER_FILLED_BUY", f"{q.symbol}: fill simulado", paper_id)
        return "OPENED_SIMULATED"
    return "BLOCKED:" + reason


def _heartbeat(store, *, at, state, cursor, selected=0, successful=0, failed=0,
               inserted=0, confirmed=0, candidates=0, detail=""):
    with store.connect() as connection:
        orders = connection.execute("SELECT real_orders_sent FROM observer_state WHERE id=1").fetchone()
        real_orders = int(orders[0]) if orders else -1
        connection.execute("""INSERT OR REPLACE INTO intraday_scalping_worker_state
          VALUES(1,?,?,?,?,?,?,?,?,?,?,?)""",
          (_stamp(at),state,cursor,selected,successful,failed,inserted,confirmed,
           candidates,real_orders,detail))


def run_worker(store, stop, *, clock_fn):
    """Proceso independiente; un error de PPI nunca detiene reloj ni salidas."""
    from bd_ppi_readonly_guard import (ProductionMarketReader, retry_read,
                                       session_invalid, classify_read_error)
    from bf_production_paper_observer import _secret
    init_schema(store)
    cursor = 0
    reader = None
    next_login = 0.0
    interval = max(60, int(os.getenv("PAPER_INTRADAY_SCAN_SECONDS", "180")))
    batch_limit = max(8, min(40, int(os.getenv("PAPER_INTRADAY_BATCH_LIMIT", "24"))))
    try:
        while not stop.is_set():
            at = aware_datetime(clock_fn())
            if not _market_open(at):
                _heartbeat(store,at=at,state="WAITING_MARKET",cursor=cursor,
                           detail="Scanner activo; espera ventana intradiaria 10:30-17:00 Argentina")
                stop.wait(20)
                continue
            if reader is None:
                if time.monotonic() < next_login:
                    _heartbeat(store,at=at,state="LOGIN_COOLDOWN",cursor=cursor)
                    stop.wait(20)
                    continue
                try:
                    reader = ProductionMarketReader(*_secret(), audit=store.audit_http)
                    reader.login_once()
                    store.event("PPI_LOGIN", "owner=scalping")
                except Exception as exc:
                    if reader:
                        reader.close()
                    reader = None
                    next_login = time.monotonic() + 900
                    _heartbeat(store,at=at,state="LOGIN_ERROR",cursor=cursor,failed=1,
                               detail=type(exc).__name__)
                    stop.wait(20)
                    continue
            selected, cursor, universe = select_batch(store,limit=batch_limit,cursor=cursor)
            successful = failed = inserted = confirmed = candidates = 0
            invalid_session = False
            for record in selected:
                if stop.is_set():
                    break
                try:
                    payload = retry_read(lambda: reader.intraday(
                        record["ticker"],record["instrument_type"],record["settlement"]), retries=1)
                    received = _stamp(clock_fn())
                    points = normalize_payload(payload,received_at=received)
                    result = persist_payload(store,record,points,received_at=received)
                    inserted += result["inserted"]
                    confirmed += int(result["state"] == "CONFIRMED_INTERVAL_VOLUME")
                    candidate_action = evaluate_candidate(store,record,at=received)
                    if candidate_action == "BUY_CANDIDATE":
                        candidates += 1
                        result_action = promote_paper_candidate(store,record,at=received)
                        store.event("SCALPING_PAPER_PROMOTION", f"{record['ticker']}: {result_action}")
                    successful += 1
                except Exception as exc:
                    failed += 1
                    store.event("INTRADAY_SCALPING_ERROR",
                                f"{record['ticker']}: {classify_read_error(exc)}")
                    if session_invalid(exc):
                        invalid_session = True
                        break
                stop.wait(0.75)
            metrics = reader.metrics if reader else {"http_blocked": 0}
            state = "SECURITY_BLOCK" if metrics.get("http_blocked") or False else \
                    "DEGRADED" if failed and successful else "ERROR" if failed else "RUNNING"
            _heartbeat(store,at=clock_fn(),state=state,cursor=cursor,selected=len(selected),
                       successful=successful,failed=failed,inserted=inserted,
                       confirmed=confirmed,candidates=candidates,
                       detail=(f"universo={universe}; lote={len(selected)}; scanner activo; "
                               f"modo={os.getenv('PAPER_SCALPING_MODE','ACTIVE_OBSERVE')}; "
                               "fills exclusivamente PAPER; órdenes reales bloqueadas"))
            if invalid_session:
                reader.close()
                reader = None
                next_login = time.monotonic() + 60
            stop.wait(interval)
    finally:
        if reader:
            reader.close()
        _heartbeat(store,at=clock_fn(),state="STOPPED",cursor=cursor,
                   detail="Proceso detenido; no se enviaron órdenes")
