"""Attribute gaps in the saved 27 November wheel by exact PAPER identity."""

from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time
from types import SimpleNamespace


START = "2026-11-27T13:30:00+00:00"
END = "2026-11-27T20:00:00+00:00"
SCHEMA = "porota.iol.wheel-phase-readonly.v1"
REFERENCE = {"run_id": 46142938923, "artifact_id": 15262631406,
             "json_sha256": "c01d826ebe3f25ca5baa5123d153403ff1a2033209a4f9aeb0b100e0b56da8d8"}
EXPECTED = {"source_sha": "28516e41b5c1aae0c183c1d55c41c13bca0fae15",
            "source_tree": "fdd24f70e9cbbbf241731a26b68c7b517e86a354",
            "image_id": "sha256:95918b793f999eb495334354b4392b8be9251daeeb02b863faf86c4ff98145b5",
            "attempt": "46060603986-1"}
FAMILIES = ("ACCIONES", "CEDEARS", "ETFS", "BONOS", "LETRAS", "OBLIGACIONES",
            "OPCIONES", "FUTUROS", "CAUCIONES", "FCI")
SPOT = {"ACCIONES", "CEDEARS", "ETFS"}
SOURCE_CODES = {"IOL_ASSET_IDENTITY_MISMATCH", "IOL_ASSET_CURRENCY_UNVERIFIED",
                "IOL_ASSET_FAMILY_UNVERIFIED", "IOL_QUOTE_CURRENCY_UNVERIFIED",
                "IOL_EXACT_CONTRACT_MISMATCH", "IOL_MARKET_CURRENCY_MISMATCH"}
CLOCK_CODES = {"IOL_BOOK_CROSSED", "IOL_BOOK_TWO_SIDED_UNVERIFIED",
               "IOL_BOOK_BEST_LEVEL_AMBIGUOUS", "IOL_LAST_TRADE_CLOCK_UNVERIFIED",
               "IOL_LAST_TRADE_STALE", "IOL_MARKET_CLOCK_IN_FUTURE"}
TRANSPORT_CODES = {"IOL_GATEWAY_RATE_HOLD", "IOL_GATEWAY_UNAVAILABLE", "IOL_HTTP_401",
                   "IOL_REST_HTTP_ERROR", "IOL_MARKET_OBSERVATION_FAILED"}
REJECTION_CODES = SOURCE_CODES | CLOCK_CODES | TRANSPORT_CODES | {"IOL_MARKET_IDENTITY_MISMATCH"}
TERM_CODES = {"PPI_SETTLEMENT_NOT_PROVIDER_ATTESTED", "PPI_NATIVE_SETTLEMENT_RECEIPT_INVALID"}
CATALOG_SOURCES = {"PPI_FIELD", "REQUEST_CANDIDATE", "PPI_PRIMARY", "IOL_COMPLEMENTARY",
                   "BYMA_PUBLIC_COMPLEMENTARY", "A3_PUBLIC_COMPLEMENTARY", "CAFCI_COMPLEMENTARY"}
GATE_CODES = {"NO_GO_PAPER_SCALPING", "EXPOSURE_LIMIT", "BUDGET_BLOCKED", "NO_AVAILABLE_CASH",
              "OPENING_NOT_AUTHORIZED", "UNDERLYING_MARKET_CLOSED"}


def exact(values):
    if (isinstance(values, (list, tuple)) and len(values) == 5 and values[1] in FAMILIES
            and all(type(value) is str and value not in {"UNKNOWN", "NO_VERIFICADO"}
                    and re.fullmatch(r"[A-Z0-9.^_()/-]{1,36}", value) for value in values)):
        return tuple(values)
    return None


def instant(value):
    try:
        clock = datetime.fromisoformat(value)
        return clock.astimezone(timezone.utc) if clock.tzinfo else None
    except (TypeError, ValueError, OverflowError):
        return None


WINDOW = (instant(START), instant(END))


def in_wheel(value):
    return (stamp := instant(value)) is not None and WINDOW[0] <= stamp < WINDOW[1]


def read_reference(path):
    with path.open("rb") as source:
        content = source.read(128 * 1024 + 1)
    if hashlib.sha256(content).hexdigest() != REFERENCE["json_sha256"]:
        raise ValueError("REFERENCE_DIGEST_MISMATCH")
    raw = json.loads(content)
    wheel = raw["runtime_paper_go"]["wheel_coverage"]
    if (raw["source_sha"] != EXPECTED["source_sha"] or raw["read_only"] is not True
            or raw["real_orders_sent"] != 0 or (wheel["start"], wheel["end_exclusive"]) != (START, END)
            or wheel["status"] != "RECORDED_SESSION_RECONCILED"):
        raise ValueError("REFERENCE_PROVENANCE_MISMATCH")
    counts = {family: {field: wheel["by_family"].get(family, {}).get(field, 0)
                       for field in ("close_catalog_eligible", "close_catalog_eligible_with_fresh_pair")}
              for family in SPOT}
    plan = wheel["freshness_rca"]["evidence"]
    return {**{axis: [key for family in FAMILIES
                     for row in wheel["by_family"].get(family, {}).get("identity_sets", {}).get(axis, ())
                     if (key := exact(row))]
               for axis in ("planned", "fresh", "evaluated")},
            "catalogo_selector": counts,
            "totales_plan": (plan["iol_plans"], plan["bound_plan_slots"])}


def bounded_read(conn, sources, name, query, parameters, limit):
    deadline = time.monotonic() + 2.5
    conn.set_progress_handler(lambda: int(time.monotonic() >= deadline), 3000)
    try:
        records = conn.execute(query, (*parameters, limit + 1)).fetchall()
    except sqlite3.Error as error:
        sources[name] = ("SQLITE_INTERRUPT" if getattr(error, "sqlite_errorname", None) == "SQLITE_INTERRUPT"
                         else "SQLITE_READ_UNVERIFIED")
        return None
    finally:
        conn.set_progress_handler(None, 0)
    sources[name] = "ROW_LIMIT" if len(records) > limit else "READ"
    return records if len(records) <= limit else None


def read_scanner(conn, sources, planned, expected_totals):
    # Same persisted plan/event evidence as #1301, retaining only exact-key links.
    cycles = bounded_read(conn, sources, "planes", """SELECT started_at,finished_at,selected_count,detail
        FROM universe_cycle_metrics WHERE julianday(started_at)>=julianday(?)
        AND julianday(started_at)<julianday(?) ORDER BY started_at,id LIMIT ?""", (START, END), 1500)
    turns = bounded_read(conn, sources, "turnos_iol", """SELECT DISTINCT cycle_started_at
        FROM symbol_cycle_metrics WHERE julianday(cycle_started_at)>=julianday(?)
        AND julianday(cycle_started_at)<julianday(?) AND instr(detail,'IOL REST')>0 LIMIT ?""",
        (START, END), 1500)
    rejects = bounded_read(conn, sources, "rechazos_scanner", """SELECT event_at,detail FROM paper_events
        WHERE event_type IN ('DATA_ERROR','DATA_REJECTED') AND julianday(event_at)>=julianday(?)
        AND julianday(event_at)<julianday(?) ORDER BY event_at,id LIMIT ?""", (START, END), 24000)
    plans = []
    rejected = defaultdict(set)
    if cycles is None or turns is None:
        return planned, rejected
    linked = {row[0] for row in turns}
    plan_complete = True
    bound_slots = 0
    for started, finished, selected_count, detail in cycles:
        try:
            plan = json.loads(detail.split("factibilidad=", 1)[1])["active_plan"]
        except (AttributeError, IndexError, KeyError, TypeError, ValueError):
            plan_complete &= started not in linked
            continue
        if not isinstance(plan, dict):
            plan_complete &= started not in linked
            continue
        if "iol_rest_budget" not in plan and started not in linked:
            continue
        begin, end = instant(started), instant(finished)
        symbols = defaultdict(set)
        reads = plan.get("read_scopes")
        if not isinstance(reads, (list, tuple)):
            plan_complete = False
            continue
        for read in reads:
            key = exact(read.get("identity", ())) if isinstance(read, dict) else None
            if key:
                symbols[key[0]].add(key)
                planned.add(key)
            else:
                plan_complete = False
        bound_count = sum(map(len, symbols.values()))
        bound_slots += bound_count
        if bound_count != selected_count:
            plan_complete = False
        if begin is not None and end is not None and begin <= end:
            plans.append((begin, end, symbols))
        else:
            plan_complete = False
    if not plans or not plan_complete or (len(plans), bound_slots) != expected_totals:
        sources["planes"] = "PLAN_PARTIAL"
    for observed, detail in rejects or ():
        stamp = instant(observed)
        if stamp is None or not isinstance(detail, str):
            continue
        symbol, _, code = detail.partition(": ")
        if code not in REJECTION_CODES:
            continue
        scopes = [symbols.get(symbol, set()) for begin, end, symbols in plans if begin <= stamp <= end]
        if len(scopes) == 1 and len(scopes[0]) == 1:
            rejected[next(iter(scopes[0]))].add(code)
    return planned, rejected


def read_catalog_selector(conn, sources, reader, expected, fresh):
    # #1301 retained the cardinalities but not the catalog membership by key.
    if reader is None:
        sources["catalogo_selector"] = "SELECTOR_NOT_READ"
        return None
    row_factory = conn.row_factory
    conn.row_factory = sqlite3.Row
    deadline = time.monotonic() + 2.5
    conn.set_progress_handler(lambda: int(time.monotonic() >= deadline), 3000)
    try:
        # The installed selector closes its handle; the wrapper keeps the
        # surrounding read transaction and its window-consistent catalog open.
        view = SimpleNamespace(connect=lambda: SimpleNamespace(execute=conn.execute, close=lambda: None))
        keys = {key for row in reader(view, now=WINDOW[1]) if (key := exact(row))}
    except (sqlite3.Error, TypeError, ValueError):
        sources["catalogo_selector"] = "SELECTOR_READ_UNVERIFIED"
        return None
    finally:
        conn.set_progress_handler(None, 0)
        conn.row_factory = row_factory
    counts = Counter(key[1] for key in keys)
    joined = Counter(key[1] for key in keys & fresh)
    if any(expected.get(family) != {"close_catalog_eligible": counts[family],
                                    "close_catalog_eligible_with_fresh_pair": joined[family]}
           for family in SPOT):
        sources["catalogo_selector"] = "SELECTOR_COUNTS_CHANGED"
        return None
    sources["catalogo_selector"] = "READ"
    return keys


def read_orders(conn, sources):
    decisions = bounded_read(conn, sources, "ordenes_gate", """SELECT d.decision_key,d.decided_at,d.symbol,
        g.final_result,g.reason,g.paper_id,
        CASE WHEN json_valid(e.payload_json) THEN json_extract(e.payload_json,'$.quote_used.symbol') END,
        CASE WHEN json_valid(e.payload_json) THEN json_extract(e.payload_json,'$.quote_used.asset_class') END,
        CASE WHEN json_valid(e.payload_json) THEN json_extract(e.payload_json,'$.quote_used.market') END,
        CASE WHEN json_valid(e.payload_json) THEN json_extract(e.payload_json,'$.quote_used.currency') END,
        CASE WHEN json_valid(e.payload_json) THEN json_extract(e.payload_json,'$.quote_used.settlement') END,
        CASE WHEN json_valid(e.payload_json) THEN json_extract(e.payload_json,'$.quote_used.observed_at') END,
        CASE WHEN json_valid(e.payload_json) THEN json_extract(e.payload_json,'$.quote_used.metadata_source') END
        FROM paper_decisions d LEFT JOIN trade_gate_evaluations g USING(decision_key)
        LEFT JOIN decision_evidence_snapshots e USING(decision_key) WHERE d.action='BUY'
        AND julianday(d.decided_at)>=julianday(?) AND julianday(d.decided_at)<julianday(?) LIMIT ?""",
        (START, END), 12000)
    fills = bounded_read(conn, sources, "fills_vida", """SELECT p.symbol,p.asset_class,p.market,p.currency,
        p.settlement,p.paper_id,p.opened_at,p.closed_at,f.side,f.filled_at FROM paper_positions p
        JOIN paper_fills f ON f.paper_id=p.paper_id WHERE f.side IN ('BUY_SIMULATED','SELL_SIMULATED')
        AND julianday(f.filled_at)>=julianday(?) AND julianday(f.filled_at)<julianday(?) LIMIT ?""",
        (START, END), 6000)
    orders, trades, unbound = defaultdict(list), defaultdict(dict), defaultdict(int)
    for row in decisions or ():
        native, at, symbol, result, reason, paper_id = row[:6]
        if not isinstance(native, str) or not in_wheel(at):
            continue
        parts = native.split(":", 6)
        if len(parts) != 7 or parts[1] != symbol or parts[6] != f"{at[:16]}:BUY":
            continue
        key = exact((parts[1], parts[2], parts[5], parts[4], parts[3]))
        if not key:
            continue
        if key != exact(row[6:11]) or at != row[11] or (key[1] in SPOT and row[12] != "IOL_REST"):
            unbound[key] += 1
            continue
        orders[key].append((result, reason if reason in GATE_CODES else "OTRO_GATE", paper_id))
    for row in fills or ():
        key = exact(row[:5])
        if not key or not in_wheel(row[9]):
            continue
        paper_id, opened, closed, side, filled = row[5:]
        if (side == "BUY_SIMULATED" and instant(filled) != instant(opened)
                or side == "SELL_SIMULATED" and instant(filled) != instant(closed)):
            continue
        trades[key].setdefault(paper_id, set()).add(side)
    return orders, trades, unbound


def first_phase(key, catalog, selector, planned, fresh, evaluated, rejects, buys, same_trade, unbound, sources):
    if key not in catalog:
        return "CATALOGO", "FILA_EXACTA_NO_ACREDITADA"
    status, stamp, _, checked, details = catalog[key]
    if not instant(stamp) or instant(stamp) >= WINDOW[1]:
        return "CATALOGO", "VIGENCIA_HISTORICA_NO_ACREDITADA"
    if status != "AVAILABLE":
        return "CATALOGO", "HOLD_DE_CATALOGO_CONSERVADO"
    if key not in fresh:
        codes = rejects.get(key, ())
        if CLOCK_CODES.intersection(codes):
            return "RELOJ_PAR", "RECHAZO_EXACTO_RELOJ_O_LIBRO"
        if "IOL_MARKET_IDENTITY_MISMATCH" in codes:
            return "FUENTE_FICHA", "VARIANTE_DE_MERCADO_NO_ACREDITADA"
        if SOURCE_CODES.intersection(codes):
            return "FUENTE_FICHA", "RECHAZO_EXACTO_DE_FICHA"
        if TRANSPORT_CODES.intersection(codes):
            return "FUENTE_FICHA", "RECHAZO_EXACTO_DE_TRANSPORTE"
        if key[1] in SPOT and key not in planned and selector is not None:
            if key not in selector:
                return "CATALOGO", "FUERA_DEL_SELECTOR_IOL_RECONCILIADO"
            if sources.get("planes") == sources.get("turnos_iol") == "READ":
                return "PLAN", "FUERA_DEL_PLAN_IOL_RECONCILIADO"
        if (key[1] not in SPOT and instant(checked) and instant(checked) < WINDOW[1]
                and TERM_CODES.intersection(details.split(";"))):
            return "PLAZO", "PLAZO_NO_CERTIFICADO_EN_PROYECCION_PREVIA"
        return "FUENTE_FICHA", ("FICHA_DE_RUEDA_NO_ACREDITADA" if key[1] in SPOT
                                else "RECIBO_ESPECIALIZADO_DE_RUEDA_NO_ACREDITADO")
    if key not in evaluated:
        return "SENAL", "PAR_SIN_EVALUACION_EXACTA_EN_RECIBO"
    if sources.get("ordenes_gate") != "READ":
        return "ORDEN_SIMULADA", "LECTURA_DE_ORDEN_NO_VERIFICADA"
    if not buys:
        return "ORDEN_SIMULADA", ("BUY_SIN_VINCULO_EXACTO_AL_RECIBO" if unbound
                                 else "SIN_BUY_EN_DECISIONES_LEIDAS")
    if not same_trade:
        code = "GATE_BLOQUEADO" if any(result == "BLOCKED" for result, _, _ in buys) else "GATE_NO_ACREDITADO"
        return "ORDEN_SIMULADA", code
    if sources.get("fills_vida") != "READ":
        return "VIDA_SALIDA", "LECTURA_DE_FILLS_NO_VERIFICADA"
    if any(sides == {"BUY_SIMULATED", "SELL_SIMULATED"} for sides in same_trade):
        return "COMPLETO", "ENTRADA_Y_SALIDA_SIMULADAS_MISMA_CLAVE_Y_POSICION"
    if any("BUY_SIMULATED" in sides for sides in same_trade):
        return "VIDA_SALIDA", "ENTRADA_SIMULADA_SIN_SALIDA_LIGADA_EN_VENTANA"
    return "ORDEN_SIMULADA", "GATE_ABIERTO_SIN_FILL_LIGADO"


def audit(conn, reference, read_selector=None):
    sources = {}
    planned, fresh, evaluated = (set(map(tuple, reference[name])) for name in ("planned", "fresh", "evaluated"))
    rows = bounded_read(conn, sources, "catalogo", """SELECT f.ticker,f.instrument_type,f.market,f.currency,
        f.settlement,f.status,f.last_seen_at,f.settlement_source,c.checked_at,COALESCE(c.detail,'')
        FROM financial_instrument_catalog f LEFT JOIN candidate_identity_v2 c
        USING(ticker,instrument_type,market,currency,settlement)
        WHERE f.instrument_type IN (?,?,?,?,?,?,?,?,?,?) LIMIT ?""", FAMILIES, 24500)
    families = {family: {"claves": 0, "catalogo_sin_clave_exacta": 0, "en_plan_iol": 0,
                         "en_selector_iol_reconciliado": None,
                         "par_iol_acreditado": 0, "senal_exacta": 0,
                         "posiciones_con_fill_buy": 0, "posiciones_con_salida_completa": 0,
                         "buy_sin_vinculo_exacto_al_recibo": 0,
                         "rondas_cerradas_ligadas": 0, "primera_fase": {},
                         "fuente_plazo_catalogo": {}, "rechazos_exactos": {}, "vetos_buy": {}}
                for family in FAMILIES}
    catalog = {}
    for row in rows or ():
        if (key := exact(row[:5])):
            catalog[key] = row[5:]
        else:
            families[row[1]]["catalogo_sin_clave_exacta"] += 1
    selector = read_catalog_selector(conn, sources, read_selector, reference.get("catalogo_selector", {}), fresh)
    if selector is not None:
        counts = Counter(key[1] for key in selector)
        for family in SPOT:
            families[family]["en_selector_iol_reconciliado"] = counts[family]
    planned, rejects = read_scanner(conn, sources, planned, reference.get("totales_plan"))
    orders, trades, unbound = read_orders(conn, sources)
    for key in catalog.keys() | planned | fresh | evaluated | orders.keys() | trades.keys() | unbound.keys():
        result = families[key[1]]
        result["claves"] += 1
        result["en_plan_iol"] += key in planned
        result["par_iol_acreditado"] += key in fresh
        result["senal_exacta"] += key in evaluated
        result["buy_sin_vinculo_exacto_al_recibo"] += unbound.get(key, 0)
        if record := catalog.get(key):
            code = record[2] if record[2] in CATALOG_SOURCES else "OTRA_FUENTE_REGISTRADA"
            result["fuente_plazo_catalogo"][code] = result["fuente_plazo_catalogo"].get(code, 0) + 1
        for code in rejects.get(key, ()):
            result["rechazos_exactos"][code] = result["rechazos_exactos"].get(code, 0) + 1
        buys = orders.get(key, ())
        for outcome, code, _ in buys:
            if outcome == "BLOCKED":
                result["vetos_buy"][code] = result["vetos_buy"].get(code, 0) + 1
        positions = trades.get(key, {})
        for sides in positions.values():
            result["posiciones_con_fill_buy"] += "BUY_SIMULATED" in sides
            result["posiciones_con_salida_completa"] += "SELL_SIMULATED" in sides
        opened = {pid for outcome, _, pid in buys if outcome == "OPENED_SIMULATED" and pid}
        same_trade = [positions.get(pid, set()) for pid in opened]
        result["rondas_cerradas_ligadas"] += sum(sides == {"BUY_SIMULATED", "SELL_SIMULATED"} for sides in same_trade)
        phase, code = first_phase(key, catalog, selector, planned, fresh, evaluated, rejects, buys,
                                  same_trade, unbound.get(key, 0), sources)
        bucket = result["primera_fase"].setdefault(phase, {})
        bucket[code] = bucket.get(code, 0) + 1
    return {"estado_lecturas": sources, "familias": families}


def remote_probe(reference):
    import os
    import sys

    source = Path.home() / ".local/state/porota-rc6/releases" / EXPECTED["source_sha"] / "source"
    if (not 0 < os.getuid() == os.geteuid() or source.stat().st_uid != os.getuid()
            or not source.is_dir() or any(path.is_symlink() for path in (source, *source.parents))):
        raise ValueError("SOURCE_CUSTODY_REQUIRED")
    sys.path.insert(0, str(source))
    from bu_instrument_catalog import iol_spot_catalog
    from scripts.porota_rootless_host import DB_RELATIVE, Identity, database_snapshot, require
    from scripts.porota_rootless_jobs import scheduler_lock, verified_host

    root = Path("/opt/porota-trading")
    identity = Identity.current()
    with scheduler_lock(identity):
        _, current, _ = verified_host(root, identity)
        require(all(current.get(key) == value for key, value in EXPECTED.items())
                and current.get("market_provider") == "IOL_REST", "INSTALLED_RELEASE_MISMATCH")
        database = root / DB_RELATIVE
        original = database_snapshot(database)
        require(original == current.get("database"), "DATABASE_IDENTITY_UNVERIFIED")
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=.3)) as conn:
            conn.execute("PRAGMA query_only=ON")
            conn.execute("BEGIN")
            state = conn.execute("SELECT mode,real_orders_sent FROM observer_state WHERE id=1").fetchone()
            require(state is not None and state[0] == "PRODUCTION_PAPER"
                    and type(state[1]) is int and state[1] == 0, "PAPER_ZERO_UNVERIFIED")
            report = audit(conn, reference, iol_spot_catalog)
        require(database_snapshot(database) == original, "DATABASE_IDENTITY_CHANGED")
    return {"schema": SCHEMA, "state": "READ_ONLY_EVIDENCE", **EXPECTED, "reference": REFERENCE,
            "observed_at": datetime.now(timezone.utc).isoformat(), "start": START, "end_exclusive": END,
            "market_requests_generated": 0, "ppi_watch_queried": False, "read_only": True,
            "mode": state[0], "real_orders_sent": state[1], **report}


def main():
    import argparse
    import os

    from scripts.porota_actions_host import connection, decode_receipt, require, ssh_run

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    receipt = {"schema": SCHEMA, "state": "NO_VERIFICADO", "reason": "REFERENCE_UNVERIFIED"}
    try:
        reference = read_reference(args.reference)
        receipt["reason"] = "HOST_READ_UNVERIFIED"
        source = Path(__file__).read_text(encoding="utf-8")
        payload = ("import json\nnamespace = {'__name__': 'readonly_wheel'}\n"
                   f"exec(compile({source!r}, 'readonly_wheel.py', 'exec'), namespace)\n"
                   f"print(json.dumps(namespace['remote_probe']({reference!r}), sort_keys=True))\n")
        with connection(os.environ) as (ssh, _, _):
            process = ssh_run(ssh, ["/usr/bin/python3", "-I", "-B", "-"],
                              data=payload.encode(), timeout=45)
        require(process.returncode == 0, "HOST_READ_UNVERIFIED")
        observed = decode_receipt(process.stdout)
        require(observed.get("schema") == SCHEMA and observed.get("state") == "READ_ONLY_EVIDENCE"
                and all(observed.get(key) == value for key, value in EXPECTED.items())
                and observed.get("reference") == REFERENCE and observed.get("read_only") is True
                and observed.get("mode") == "PRODUCTION_PAPER"
                and all(type(observed.get(key)) is int and observed[key] == 0
                        for key in ("real_orders_sent", "market_requests_generated"))
                and observed.get("ppi_watch_queried") is False,
                "HOST_BOUNDARY_UNVERIFIED")
        receipt = observed
        return 0
    except (KeyError, ValueError, OSError):
        return 1
    finally:
        args.output.write_text(json.dumps(receipt, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
