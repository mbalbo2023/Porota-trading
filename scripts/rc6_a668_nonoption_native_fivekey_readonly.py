#!/usr/bin/env python3
"""A668: strict non-option PPI-native five-key source contract SQL READ_ONLY.

This diagnoses only BONOS/OBLIGACIONES/FCI currently lacking exact Contract
Evidence V2 Current. A PPI_PRIMARY metadata label is NOT five native fields,
source authentication, a fresh book, an economic contract, or PAPER GO.
No provider GET, broker, PPI Watch, order, copy of DB/WAL, or DB mutation.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
import sys
import time
import unicodedata
from urllib.parse import quote
import uuid


SCHEMA = "rc6.a668.nonoption-native-fivekey-readonly.v1"
DB = Path("/opt/porota-trading/data/paper_v17/observer_v17.db")
FAMILIES = frozenset({"BONOS", "OBLIGACIONES", "FCI"})
ACTIVE = frozenset({"AVAILABLE", "ACTIVE", "READY"})
CLAIMS = frozenset({"PPI_PRIMARY", "OTHER_OR_ABSENT"})
CLASSES = frozenset({
    "NATIVE_FIVE_STRUCTURAL_MATCH", "NOT_PPI_PRIMARY",
    "SETTLEMENT_NOT_PROVIDER_ATTESTED", "NATIVE_FIELD_MISSING",
    "NATIVE_FIELD_MISMATCH", "NATIVE_CURRENCY_UNRECOGNIZED",
    "NATIVE_FIVE_CLOCK_HOLD", "MALFORMED_METADATA",
})
NATIVE_FIELDS = ("ticker", "type", "market", "currency", "settlement")
MAX_ROWS = 30000
MAX_GROUPS = 96
MAX_RAW = 16384
MAX_METADATA_BYTES = 48 * 1024 * 1024
MAX_COUNT = 1000000
FAMILY_ALIASES = {
    "ACCION": "ACCIONES", "EQUITY": "ACCIONES", "CEDEAR": "CEDEARS",
    "ETF": "ETFS", "BONO": "BONOS", "TITULOSPUBLICOS": "BONOS",
    "LETRA": "LETRAS", "ON": "OBLIGACIONES",
    "OBLIGACIONESNEGOCIABLES": "OBLIGACIONES", "OPCION": "OPCIONES",
    "OPTIONS": "OPCIONES", "FUTURO": "FUTUROS", "FUTURES": "FUTUROS",
    "CAUCION": "CAUCIONES", "LEBAC": "LETRAS", "NOBAC": "LETRAS",
    "ACCIONESUSA": "ACCIONES", "FCIEXTERIOR": "FCI",
}
FAMILY_FALLBACKS = {
    "TIT. PUBLICOS": "BONOS", "OBL.NEG.": "OBLIGACIONES",
    "OBL.PYME": "OBLIGACIONES", "FONDO COMÚN DE INVERSIÓN": "FCI",
    "FDOS.INV": "FCI", "FONDOS COTIZ.": "ETFS", "LETRA / NOTA": "LETRAS",
}
KNOWN_FAMILIES = frozenset({
    "ACCIONES", "CEDEARS", "ETFS", "BONOS", "LETRAS", "OBLIGACIONES",
    "OPCIONES", "FUTUROS", "CAUCIONES", "FCI",
})
MARKET_ALIAS = {"BCBA": "BYMA", "BYMA": "BYMA", "ROFEX": "A3", "A3": "A3"}
TERM_ALIAS = {"T1": "A-24HS", "24HS": "A-24HS", "A-24HS": "A-24HS",
              "T0": "INMEDIATA", "CI": "INMEDIATA", "INMEDIATA": "INMEDIATA"}
DEFAULT_SETTLEMENT = {
    "ACCIONES": "A-24HS", "CEDEARS": "A-24HS", "ETFS": "A-24HS",
    "BONOS": "A-24HS", "LETRAS": "A-24HS", "OBLIGACIONES": "A-24HS",
    "OPCIONES": "INMEDIATA", "FUTUROS": "INMEDIATA",
    "CAUCIONES": "INMEDIATA", "FCI": "INMEDIATA",
}
CURRENCIES = frozenset({"ARS", "USD", "USD_MEP", "USD_CCL"})


class Hold(Exception):
    """Fixed fail-closed reason, never raw database/provider text."""


def _base():
    return {"schema": SCHEMA, "state": "NO_VERIFICADO", "reason": "DB_RO_UNAVAILABLE",
            "diagnostic_class": "STRUCTURAL_ONLY_NOT_PAPER_GO",
            "a626_historical_row_attribution": "NOT_VERIFIED",
            "read_only": True, "private_ro_mount": False,
            "paper_workspace_verified": False, "paper_admission_certified": False,
            "runtime_sha_verified": False, "provider_gets": 0,
            "orders_called": False, "ppi_watch_touched": False,
            "real_orders_sent": None, "sampled_at_utc": None,
            "target_total": None, "target_by_family": {},
            "catalog_active_gap_by_family": {},
            "catalog_all_by_family": {}, "missing_literals_by_family": {}, "joint": []}


def _mount_ro(path):
    chosen, readonly = -1, False
    try:
        for line in Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines():
            fields = line.split(" - ", 1)[0].split()
            if len(fields) < 6:
                continue
            point = fields[4].replace("\\040", " ")
            if point != "/" and path != Path(point) and not str(path).startswith(point.rstrip("/") + "/"):
                continue
            if len(point) > chosen:
                chosen, readonly = len(point), "ro" in fields[5].split(",")
    except (OSError, UnicodeError, ValueError):
        return False
    return chosen >= 0 and readonly


def _cols(conn, table):
    return {r[1] for r in conn.execute("PRAGMA table_info(" + table + ")")}


def _utc(value):
    if not isinstance(value, str) or not 0 < len(value) <= 64:
        raise Hold("CATALOG_CLOCK_HOLD")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.utcoffset() is None:
            raise ValueError("naive")
        return dt.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        raise Hold("CATALOG_CLOCK_HOLD") from None


def _json_strict(text):
    def pairs_unique(pairs):
        result = {}
        for k, v in pairs:
            if k in result:
                raise ValueError("duplicate key")
            result[k] = v
        return result
    try:
        return json.loads(text, object_pairs_hook=pairs_unique,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")))
    except (TypeError, ValueError):
        raise Hold("CATALOG_JSON_HOLD") from None


def _family(value):
    raw = str(value or "").strip().upper()
    compact = raw.replace("_", "").replace("-", "").replace(" ", "")
    aliased = FAMILY_ALIASES.get(compact, compact)
    if aliased in KNOWN_FAMILIES:
        return aliased
    return FAMILY_FALLBACKS.get(raw, raw)


def _market(value):
    v = str(value or "").strip().upper()
    return MARKET_ALIAS.get(v, v)


def _settlement(value, family=""):
    v = str(value or "").strip().upper()
    return TERM_ALIAS.get(v, v) if v else DEFAULT_SETTLEMENT.get(_family(family), "")


def _currency(value):
    v = " ".join(str(value or "").strip().upper().split())
    v = "".join(ch for ch in unicodedata.normalize("NFD", v)
                if not unicodedata.combining(ch))
    v = {"PESOS": "ARS", "PESO ARGENTINO": "ARS",
         "DOLARES BILLETE | MEP": "USD_MEP",
         "DOLARES DIVISA | CCL": "USD_CCL"}.get(v, v)
    if v not in CURRENCIES:
        raise ValueError("unrecognized currency")
    return v


def _identity(row):
    return tuple(str(row[col] or "").strip().upper()
                 for col in ("instrument_type", "ticker", "market", "currency", "settlement"))


def _literals(raw):
    return {"ticker": raw.get("ticker") or raw.get("symbol"),
            "type": raw.get("type") or raw.get("instrumentType"),
            "market": raw.get("market"), "currency": raw.get("currency"),
            "settlement": raw.get("settlement")}


def _classify(row, raw, *, now):
    if raw is None:
        return "MALFORMED_METADATA", "OTHER_OR_ABSENT", set(NATIVE_FIELDS)
    claim = ("PPI_PRIMARY" if str(raw.get("_discovery_source") or "").upper() == "PPI_PRIMARY"
             else "OTHER_OR_ABSENT")
    values = _literals(raw)
    missing = {name for name, value in values.items() if value in (None, "")}
    if str(row["settlement_source"] or "").upper() != "PPI_FIELD":
        return "SETTLEMENT_NOT_PROVIDER_ATTESTED", claim, missing
    if claim != "PPI_PRIMARY":
        return "NOT_PPI_PRIMARY", claim, missing
    if missing:
        return "NATIVE_FIELD_MISSING", claim, missing
    try:
        observed = (str(values["ticker"]).strip().upper(), _family(values["type"]),
                    _market(values["market"]), _currency(values["currency"]),
                    _settlement(values["settlement"], values["type"]))
        expected = (str(row["ticker"] or "").strip().upper(),
                    _family(row["instrument_type"]), _market(row["market"]),
                    str(row["currency"] or "").strip().upper(),
                    _settlement(row["settlement"], row["instrument_type"]))
    except (ValueError, TypeError):
        return "NATIVE_CURRENCY_UNRECOGNIZED", claim, missing
    if observed != expected:
        return "NATIVE_FIELD_MISMATCH", claim, missing
    try:
        if _utc(row["last_seen_at"]) > now:
            return "NATIVE_FIVE_CLOCK_HOLD", claim, missing
    except Hold:
        return "NATIVE_FIVE_CLOCK_HOLD", claim, missing
    return "NATIVE_FIVE_STRUCTURAL_MATCH", claim, missing


def _workspace(conn):
    x = conn.execute("SELECT id,namespace,origin,imported_legacy,dataset_id FROM paper_workspace LIMIT 2").fetchall()
    if len(x) != 1:
        return False
    row = x[0]
    try:
        return (tuple(row[:4]) == (1, "POROTA_PAPER_V17_FRESH", "FRESH_EMPTY", 0)
                and str(uuid.UUID(row[4])) == row[4])
    except (ValueError, TypeError, AttributeError):
        return False


def _aggregate(conn):
    required = {
        "paper_workspace": {"id", "namespace", "origin", "imported_legacy", "dataset_id"},
        "observer_state": {"id", "real_orders_sent"},
        "financial_instrument_catalog": {"ticker", "instrument_type", "market",
                                         "currency", "settlement", "settlement_source",
                                         "status", "capability", "metadata_json", "last_seen_at"},
        "contract_evidence_v2_current": {"family", "ticker", "market", "currency", "settlement"},
    }
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not required.keys() <= tables or any(not needed <= _cols(conn, table)
                                            for table, needed in required.items()):
        raise Hold("REQUIRED_SCHEMA_HOLD")
    if not _workspace(conn):
        raise Hold("PAPER_WORKSPACE_HOLD")
    state = conn.execute("SELECT real_orders_sent FROM observer_state WHERE id=1").fetchone()
    if state is None or type(state[0]) is not int or state[0] != 0:
        raise Hold("PAPER_ORDER_COUNTER_HOLD")
    currents = conn.execute("""SELECT family,ticker,market,currency,settlement
      FROM contract_evidence_v2_current LIMIT ?""", (MAX_ROWS + 1,)).fetchall()
    if len(currents) > MAX_ROWS:
        raise Hold("V2_CURRENT_ROWS_LIMIT_HOLD")
    seen_current = {
        tuple(str(value or "").strip().upper() for value in row)
        for row in currents
    }
    rows = conn.execute("""SELECT ticker,instrument_type,market,currency,settlement,
      settlement_source,status,capability,metadata_json,last_seen_at
      FROM financial_instrument_catalog
      WHERE instrument_type IN ('BONOS','OBLIGACIONES','FCI') LIMIT ?""",
      (MAX_ROWS + 1,)).fetchall()
    if len(rows) > MAX_ROWS:
        raise Hold("CATALOG_ROWS_LIMIT_HOLD")
    coverage = {f: Counter({"all": 0, "active": 0, "ready": 0, "active_gap": 0})
                for f in FAMILIES}
    targets = Counter()
    missing_by_family = {f: Counter({name: 0 for name in NATIVE_FIELDS}) for f in FAMILIES}
    groups = Counter()
    seen_catalog = set()
    bytes_read = 0
    now = datetime.now(timezone.utc)
    for row in rows:
        key = _identity(row)
        if key in seen_catalog:
            raise Hold("CATALOG_DUPLICATE_IDENTITY_HOLD")
        seen_catalog.add(key)
        family = key[0]
        coverage[family]["all"] += 1
        if str(row["status"] or "").upper() not in ACTIVE:
            continue
        coverage[family]["active"] += 1
        if str(row["capability"] or "").startswith("READY_PAPER_"):
            coverage[family]["ready"] += 1
            continue
        coverage[family]["active_gap"] += 1
        if key in seen_current:
            continue
        targets[family] += 1
        raw_text = row["metadata_json"]
        if not isinstance(raw_text, str) or not 0 < len(raw_text) <= 32768:
            raw = None
        else:
            bytes_read += len(raw_text)
            if bytes_read > MAX_METADATA_BYTES:
                raise Hold("CATALOG_METADATA_BUDGET_HOLD")
            try:
                raw = _json_strict(raw_text)
                if not isinstance(raw, dict):
                    raw = None
            except Hold:
                raw = None
        classification, claim, missing = _classify(row, raw, now=now)
        for field in missing:
            missing_by_family[family][field] += 1
        groups[(family, classification, claim)] += 1
    if len(groups) > MAX_GROUPS:
        raise Hold("GROUPS_LIMIT_HOLD")
    if (any(coverage[family]["all"] == 0 for family in FAMILIES)
            or sum(c["active_gap"] for c in coverage.values()) == 0):
        raise Hold("ACTIVE_GAP_DENOMINATOR_HOLD")
    return ({f: targets[f] for f in sorted(FAMILIES)},
            {f: dict(sorted(coverage[f].items())) for f in sorted(FAMILIES)},
            {f: dict(sorted(missing_by_family[f].items())) for f in sorted(FAMILIES)},
            [dict(family=k[0], **{"class": k[1]}, claim=k[2], count=n)
             for k, n in sorted(groups.items())])


def diagnose(db=DB, *, enforce_ro_mount=True):
    report = _base()
    conn = None
    try:
        if not db.is_absolute() or db.is_symlink():
            raise Hold("DB_PATH_HOLD")
        if enforce_ro_mount and not _mount_ro(db):
            raise Hold("PRIVATE_RO_MOUNT_REQUIRED")
        report["private_ro_mount"] = bool(enforce_ro_mount)
        conn = sqlite3.connect("file:" + quote(str(db), safe="/") + "?mode=ro",
                               uri=True, timeout=0.35, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        conn.execute("PRAGMA trusted_schema=OFF")
        conn.execute("BEGIN")
        start = time.monotonic()
        conn.set_progress_handler(lambda: int(time.monotonic() - start > 19), 1000)
        targets, coverage, missing, groups = _aggregate(conn)
        report.update(state="READ_ONLY_OBSERVED", reason="STRUCTURAL_ONLY_NOT_PAPER_GO",
                      paper_workspace_verified=True, real_orders_sent=0,
                      sampled_at_utc=datetime.now(timezone.utc).isoformat(),
                      target_total=sum(targets.values()), target_by_family=targets,
                      catalog_active_gap_by_family={k: v["active_gap"] for k, v in coverage.items()},
                      catalog_all_by_family={k: v["all"] for k, v in coverage.items()},
                      missing_literals_by_family=missing, joint=groups)
    except Hold as exc:
        report["reason"] = str(exc)
    except (sqlite3.Error, OSError, TypeError, ValueError, OverflowError):
        report["reason"] = "DB_RO_QUERY_HOLD"
    finally:
        if conn is not None:
            conn.close()
    return report


def sanitize(value):
    result = _base()
    if (not isinstance(value, dict) or value.get("schema") != SCHEMA
            or value.get("state") != "READ_ONLY_OBSERVED"
            or value.get("diagnostic_class") != "STRUCTURAL_ONLY_NOT_PAPER_GO"
            or value.get("a626_historical_row_attribution") != "NOT_VERIFIED"
            or value.get("read_only") is not True
            or value.get("private_ro_mount") is not True
            or value.get("paper_workspace_verified") is not True
            or value.get("real_orders_sent") != 0
            or type(value.get("real_orders_sent")) is not int
            or value.get("provider_gets") != 0 or value.get("orders_called") is not False
            or value.get("ppi_watch_touched") is not False
            or value.get("paper_admission_certified") is not False
            or value.get("runtime_sha_verified") is not False):
        result["reason"] = "UNTRUSTED_HOST_RESPONSE"
        return result
    try:
        stamp = _utc(value.get("sampled_at_utc"))
        target = value.get("target_by_family")
        gap = value.get("catalog_active_gap_by_family")
        all_count = value.get("catalog_all_by_family")
        missing = value.get("missing_literals_by_family")
        if any(not isinstance(item, dict) or set(item) != FAMILIES
               for item in (target, gap, all_count, missing)):
            raise Hold("UNTRUSTED_DENOMINATOR")
        for family in FAMILIES:
            if (any(type(item[family]) is not int or not 0 <= item[family] <= MAX_COUNT
                    for item in (target, gap, all_count))
                    or not all_count[family] > 0 or gap[family] > all_count[family]
                    or target[family] > gap[family]
                    or not isinstance(missing[family], dict)
                    or set(missing[family]) != set(NATIVE_FIELDS)
                    or any(type(v) is not int or not 0 <= v <= target[family]
                           for v in missing[family].values())):
                raise Hold("UNTRUSTED_DENOMINATOR")
        joint = value.get("joint")
        if not isinstance(joint, list) or len(joint) > MAX_GROUPS:
            raise Hold("UNTRUSTED_GROUPS")
        seen = set()
        tallies = Counter()
        groups = []
        for item in joint:
            if not isinstance(item, dict) or set(item) != {"family", "class", "claim", "count"}:
                raise Hold("UNTRUSTED_GROUPS")
            key = (item["family"], item["class"], item["claim"])
            count = item["count"]
            if (key in seen or key[0] not in FAMILIES or key[1] not in CLASSES
                    or key[2] not in CLAIMS or type(count) is not int
                    or not 0 < count <= MAX_COUNT):
                raise Hold("UNTRUSTED_GROUPS")
            seen.add(key)
            tallies[key[0]] += count
            groups.append({"family": key[0], "class": key[1], "claim": key[2], "count": count})
        if (sum(target.values()) != value.get("target_total")
                or type(value.get("target_total")) is not int
                or sum(target.values()) > MAX_COUNT
                or any(tallies[family] != target[family] for family in FAMILIES)):
            raise Hold("UNTRUSTED_TARGET_TOTAL")
        result.update(state="READ_ONLY_OBSERVED", reason="STRUCTURAL_ONLY_NOT_PAPER_GO",
                      private_ro_mount=True, paper_workspace_verified=True,
                      real_orders_sent=0, sampled_at_utc=stamp.isoformat(),
                      target_total=sum(target.values()),
                      target_by_family=dict(sorted(target.items())),
                      catalog_active_gap_by_family=dict(sorted(gap.items())),
                      catalog_all_by_family=dict(sorted(all_count.items())),
                      missing_literals_by_family={f: dict(sorted(missing[f].items()))
                                                  for f in sorted(FAMILIES)},
                      joint=sorted(groups, key=lambda x: (x["family"], x["class"], x["claim"])))
    except (Hold, ValueError, TypeError, KeyError):
        result["reason"] = "UNTRUSTED_AGGREGATE"
    return result


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if args == ["host-probe"]:
        print(json.dumps(diagnose(), sort_keys=True, separators=(",", ":")))
        return 0
    if len(args) == 3 and args[0] == "sanitize":
        raw, target = Path(args[1]), Path(args[2])
        safe = _base()
        try:
            if 0 < raw.stat().st_size <= MAX_RAW and not raw.is_symlink():
                safe = sanitize(_json_strict(raw.read_text(encoding="utf-8")))
        except (OSError, UnicodeError, TypeError, ValueError, Hold):
            pass
        target.write_text(json.dumps(safe, sort_keys=True, separators=(",", ":")) + "\n",
                          encoding="utf-8")
        print("A668_NONOPTION_FIVEKEY=" + safe["state"])
        return 0 if safe["state"] == "READ_ONLY_OBSERVED" else 2
    raise SystemExit("A668_HOST_PROBE_OR_RUNNER_SANITIZE_ONLY")


if __name__ == "__main__":
    raise SystemExit(main())
