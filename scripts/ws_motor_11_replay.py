#!/usr/bin/env python3
"""Deterministic WS-MOTOR-12 replay on a disposable controlled SQLite copy.

The fixture contains only sanitized observations inherited from WS09/WS10.  It
does not connect to a broker, read a productive database, or expose an order
surface.  Unknown fields stay unknown; no value is copied between instruments.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import cp_contract_evidence_v2_hf6 as evidence_v2
import cq_family_contract_rules_hf6 as rules
import rc6_broker_parity_evidence as parity
import rc6_contract_bridge as bridge
import bu_instrument_catalog as catalog


REPLAY_AT = datetime(2026, 9, 28, 18, 0, tzinfo=timezone.utc)
CAPTURE_AT = "2026-09-28T17:55:00+00:00"
REAL_ROUTES_USED = ()


class Store:
    def __init__(self, path):
        self.path = str(path)

    def connect(self):
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection


def witness(family, ticker, market, currency, settlement, *, ppi_identity, note=""):
    return {
        "family": family, "ticker": ticker, "market": market,
        "currency": currency, "settlement": settlement,
        "ppi_identity": bool(ppi_identity), "note": note,
    }


WITNESSES = (
    witness("BONOS", "GD30", "BYMA", "ARS", "A-24HS", ppi_identity=True),
    witness("BONOS", "AL30", "BYMA", "ARS", "A-24HS", ppi_identity=True),
    witness("LETRAS", "D30N6", "BYMA", "ARS", "A-24HS", ppi_identity=False,
            note="IOL witness; exact current PPI identity not independently recaptured"),
    witness("ON", "YMCJO", "BYMA", "ARS", "A-24HS", ppi_identity=True),
    witness("ON", "YMCIO", "BYMA", "ARS", "A-24HS", ppi_identity=False,
            note="additional ON witness; no extrapolation from YMCJO"),
    witness("LETRAS", "S31O6", "BYMA", "ARS", "A-24HS", ppi_identity=False,
            note="additional LETRA witness pending exact PPI binding"),
    witness("OPCIONES", "GFGC6000OC", "BYMA", "ARS", "INMEDIATA", ppi_identity=True),
    witness("OPCIONES", "GFGC7000OC", "BYMA", "ARS", "INMEDIATA", ppi_identity=True),
    witness("OPCIONES", "AAPC1000O", "BYMA", "ARS", "INMEDIATA", ppi_identity=False,
            note="CEDEAR option witness pending exact PPI series binding"),
    witness("OPCIONES", "GFGC50000A", "BYMA", "ARS", "INMEDIATA", ppi_identity=False,
            note="adjusted-looking series intentionally fail-closed"),
    witness("CAUCIONES", "CAUCION_ARS_1D_COLOCADORA", "BYMA", "ARS", "A-24HS", ppi_identity=True),
    witness("CAUCIONES", "CAUCION_ARS_2D_COLOCADORA", "BYMA", "ARS", "A-48HS", ppi_identity=True),
    witness("CAUCIONES", "CAUCION_ARS_3D_COLOCADORA", "BYMA", "ARS", "A-72HS", ppi_identity=True),
    witness("CAUCIONES", "CAUCION_USD_1D_COLOCADORA", "BYMA", "USD", "A-24HS", ppi_identity=True),
    witness("FCI", "ADCAP.AP.A", "FCI", "ARS", "INMEDIATA", ppi_identity=True,
            note="PPI exact primary identity: Adcap Ahorro Pesos Clase A"),
    witness("FCI", "IOLCAMA", "FCI", "ARS", "INMEDIATA", ppi_identity=False,
            note="IOL inventory cannot create PPI identity"),
    witness("FCI", "ADCUSAD", "FCI", "USD", "INMEDIATA", ppi_identity=False,
            note="USD fund inventory cannot create PPI identity"),
    witness("FUTUROS", "DLR/DIC26", "ROFEX", "ARS", "INMEDIATA", ppi_identity=True),
    witness("FUTUROS", "DLR/ENE27", "ROFEX", "ARS", "INMEDIATA", ppi_identity=False,
            note="no term extrapolation from DLR/DIC26"),
    witness("FUTUROS", "RFX20/OCT26", "ROFEX", "ARS", "INMEDIATA", ppi_identity=False,
            note="different A3 contract; no DLR multiplier extrapolation"),
)


def obs(value, source, ref, *, unit=None, provider_at=None, derivation=None):
    return {
        "value": value, "source_class": source, "source_ref": ref,
        "unit": unit, "provider_timestamp": provider_at,
        "capture_timestamp": CAPTURE_AT,
        "freshness_basis": "PROVIDER_TIMESTAMP" if provider_at else "CAPTURE_TIMESTAMP_STATIC_ONLY",
        "derivable": bool(derivation), "derivation_rule": derivation,
    }


# Sparse on purpose.  These are observations, not templates.  A missing key is
# emitted as UNRESOLVED for that exact instrument.
KNOWN = {
    "GD30": {
        "quantity_min": obs(1, "PPI_AUTHENTICATED_DOM", "WS10:PPI:GD30-order-form", unit="VN"),
        "quantity_step": obs(1, "PPI_AUTHENTICATED_DOM", "WS10:PPI:GD30-order-form", unit="VN"),
        "price_quote_unit": obs(100, "PPI_AUTHENTICATED_DOM", "WS10:PPI:GD30-quote", unit="VN"),
        "cash_multiplier": obs(0.01, "DERIVED_OFFICIAL_RULE", "WS11:derive:GD30-price-per-100", unit="cash/price/VN",
                               derivation="1 / verified price_quote_unit 100"),
        "maturity_date": obs("2030-07-09", "IOL_STRUCTURED_API", "WS09:IOL:GD30:analytics"),
    },
    "D30N6": {
        "maturity_date": obs("2026-11-30", "IOL_STRUCTURED_API", "WS09:IOL:D30N6:info"),
        "price_quote_unit": obs(100, "IOL_STRUCTURED_API", "WS09:IOL:D30N6:info", unit="VN"),
        "cash_multiplier": obs(0.01, "DERIVED_OFFICIAL_RULE", "WS11:derive:D30N6-price-per-100", unit="cash/price/VN",
                               derivation="1 / verified price_quote_unit 100"),
    },
    "YMCJO": {
        "maturity_date": obs("2033-09-30", "IOL_STRUCTURED_API", "WS09:IOL:YMCJO:info"),
        "price_quote_unit": obs(100, "IOL_STRUCTURED_API", "WS09:IOL:YMCJO:info", unit="VN"),
        "cash_multiplier": obs(0.01, "DERIVED_OFFICIAL_RULE", "WS11:derive:YMCJO-price-per-100", unit="cash/price/VN",
                               derivation="1 / verified price_quote_unit 100"),
    },
    "GFGC6000OC": {
        "underlying": obs("GGAL", "IOL_STRUCTURED_API", "WS09:IOL:GGAL:option-chain"),
        "put_call": obs("CALL", "IOL_STRUCTURED_API", "WS09:IOL:GGAL:option-chain"),
        "strike": obs(6000, "IOL_STRUCTURED_API", "WS09:IOL:GGAL:option-chain", unit="ARS"),
        "lot_size": obs(100, "PPI_AUTHENTICATED_DOM", "WS10:PPI:GFGC6000OC-order-form", unit="underlying/contract"),
        "contract_multiplier": obs(100, "PPI_AUTHENTICATED_DOM", "WS10:PPI:GFGC6000OC-order-form", unit="underlying/contract"),
        "cash_multiplier": obs(100, "PPI_AUTHENTICATED_DOM", "WS10:PPI:GFGC6000OC-order-form", unit="underlying/contract"),
        "expiry_at": obs("2026-10-16T15:30:00-03:00", "IOL_STRUCTURED_API", "WS09:IOL:GGAL:option-chain"),
        "quantity_min": obs(1, "PPI_AUTHENTICATED_DOM", "WS10:PPI:GFGC6000OC-order-form", unit="contract"),
        "quantity_step": obs(1, "PPI_AUTHENTICATED_DOM", "WS10:PPI:GFGC6000OC-order-form", unit="contract"),
    },
    "GFGC7000OC": {
        "underlying": obs("GGAL", "IOL_STRUCTURED_API", "WS09:IOL:GGAL:option-chain"),
        "put_call": obs("CALL", "IOL_STRUCTURED_API", "WS09:IOL:GGAL:option-chain"),
        "strike": obs(7000, "IOL_STRUCTURED_API", "WS09:IOL:GGAL:option-chain", unit="ARS"),
    },
    "CAUCION_ARS_1D_COLOCADORA": {
        "side": obs("COLOCADORA", "PPI_AUTHENTICATED_DOM", "WS10:PPI:cauciones"),
        "term_days": obs(1, "IOL_STRUCTURED_API", "WS11:IOL:get_caucion_rates:ARS", unit="day"),
        "minimum_principal": obs(100000, "IOL_STRUCTURED_API", "WS11:IOL:get_caucion_rates:ARS", unit="ARS"),
        "annual_rate_fraction": obs(0.159, "IOL_STRUCTURED_API", "WS11:IOL:get_caucion_rates:ARS", unit="fraction/year"),
        "maturity_at": obs("2026-09-30", "IOL_STRUCTURED_API", "WS11:IOL:get_caucion_rates:ARS"),
    },
    "CAUCION_ARS_2D_COLOCADORA": {
        "side": obs("COLOCADORA", "PPI_AUTHENTICATED_DOM", "WS10:PPI:cauciones"),
        "term_days": obs(2, "IOL_STRUCTURED_API", "WS11:IOL:get_caucion_rates:ARS", unit="day"),
        "annual_rate_fraction": obs(0, "IOL_STRUCTURED_API", "WS11:IOL:get_caucion_rates:ARS", unit="fraction/year"),
    },
    "CAUCION_ARS_3D_COLOCADORA": {
        "side": obs("COLOCADORA", "PPI_AUTHENTICATED_DOM", "WS10:PPI:cauciones"),
        "term_days": obs(3, "IOL_STRUCTURED_API", "WS11:IOL:get_caucion_rates:ARS", unit="day"),
        "annual_rate_fraction": obs(0.15, "IOL_STRUCTURED_API", "WS11:IOL:get_caucion_rates:ARS", unit="fraction/year"),
    },
    "CAUCION_USD_1D_COLOCADORA": {
        "side": obs("COLOCADORA", "PPI_AUTHENTICATED_DOM", "WS10:PPI:cauciones"),
        "term_days": obs(1, "IOL_STRUCTURED_API", "WS11:IOL:get_caucion_rates:USD", unit="day"),
        "minimum_principal": obs(100, "IOL_STRUCTURED_API", "WS11:IOL:get_caucion_rates:USD", unit="USD"),
        "annual_rate_fraction": obs(0.002, "IOL_STRUCTURED_API", "WS11:IOL:get_caucion_rates:USD", unit="fraction/year"),
    },
    "ADCAP.AP.A": {
        "currency": obs("ARS", "PPI_STRUCTURED_API", "WS12:PPI:ADCAP.AP.A:catalog"),
        "subscription_min": obs(1000, "PPI_OFFICIAL_DOCUMENTATION", "WS12:PPI:SupermercadoFCI:desde-1000", unit="ARS"),
        "subscription_step": obs(1, "PPI_AUTHENTICATED_DOM", "WS10:PPI:Adcap-Ahorro-Pesos", unit="ARS"),
        "cutoff_time": obs("BROKER_PAGE_OBSERVED", "PPI_AUTHENTICATED_DOM", "WS10:PPI:Adcap-Ahorro-Pesos"),
        "redemption_term": obs("BROKER_PAGE_OBSERVED", "PPI_AUTHENTICATED_DOM", "WS10:PPI:Adcap-Ahorro-Pesos"),
        "nav_unit": obs("CUOTAPARTE", "PPI_AUTHENTICATED_DOM", "WS10:PPI:Adcap-Ahorro-Pesos"),
    },
    "IOLCAMA": {"currency": obs("ARS", "IOL_STRUCTURED_API", "WS11:IOL:get_fci_funds")},
    "ADCUSAD": {"currency": obs("USD", "IOL_STRUCTURED_API", "WS11:IOL:get_fci_funds")},
    "DLR/DIC26": {
        "underlying": obs("USDARS", "A3_OFFICIAL_DOCUMENTATION", "WS09:A3:DLR-contract-guide"),
        "contract_multiplier": obs(1000, "A3_OFFICIAL_DOCUMENTATION", "WS09:A3:DLR-contract-guide", unit="USD/contract"),
        "cash_multiplier": obs(1000, "A3_OFFICIAL_DOCUMENTATION", "WS09:A3:DLR-contract-guide", unit="USD/contract"),
        "min_price_increment": obs(0.5, "A3_OFFICIAL_DOCUMENTATION", "WS09:A3:DLR-contract-guide", unit="ARS/USD"),
        "tick_value": obs(500, "DERIVED_OFFICIAL_RULE", "WS09:A3:DLR-multiplier-times-tick", unit="ARS/contract",
                          derivation="1000 USD/contract * 0.5 ARS/USD"),
    },
}


FIELD_META = {
    "market": ("identity", "exact venue binding"),
    "currency": ("identity", "cash and risk ledger denomination"),
    "settlement": ("identity", "cash/asset availability date"),
    "quantity_min": ("quantity", "order validation and sizing"),
    "quantity_step": ("quantity", "rounding and order validation"),
    "principal_min": ("currency", "caucion principal validation"),
    "principal_step": ("currency", "caucion principal rounding"),
    "price_quote_unit": ("nominal", "fixed-income cash conversion"),
    "cash_multiplier": ("cash/price/quantity", "notional, risk, cash and P&L"),
    "contract_multiplier": ("underlying/contract", "derivative P&L and exposure"),
    "lot_size": ("underlying/contract", "option premium and exposure"),
    "price_tick": ("price", "limit-price validation"),
    "min_price_increment": ("price", "limit-price validation"),
    "tick_value": ("currency/contract", "P&L and margin simulation"),
    "margin_requirement": ("currency/contract", "paper margin reserve"),
    "tna": ("percent", "caucion interest accrual"),
    "nav_value": ("currency/cuota", "fund paper subscription/redemption"),
    "nav_date": ("date", "fund NAV freshness"),
}


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _create_controlled_snapshot(path):
    with sqlite3.connect(path) as connection:
        connection.execute("""CREATE TABLE controlled_catalog(
          family TEXT,ticker TEXT,market TEXT,currency TEXT,settlement TEXT,
          ppi_identity INTEGER,note TEXT)""")
        connection.executemany("INSERT INTO controlled_catalog VALUES(?,?,?,?,?,?,?)", [
            (w["family"], w["ticker"], w["market"], w["currency"], w["settlement"],
             int(w["ppi_identity"]), w["note"]) for w in WITNESSES
        ])


def _identity_observations(item):
    if not item["ppi_identity"]:
        return {}
    return {
        "market": obs(item["market"], "PPI_STRUCTURED_API", "WS09/10:PPI:catalog"),
        "currency": obs(item["currency"], "PPI_STRUCTURED_API", "WS09/10:PPI:catalog"),
        "settlement": obs(item["settlement"], "PPI_STRUCTURED_API", "WS09/10:PPI:catalog"),
    }


def _observations(item):
    return {**_identity_observations(item), **KNOWN.get(item["ticker"], {})}


def _records_for(store, item):
    return evidence_v2.current_records(
        store, family=item["family"], ticker=item["ticker"],
        currency=item["currency"], settlement=item["settlement"])


def _ingest(store, item):
    if not item["ppi_identity"]:
        return 0
    grouped = defaultdict(dict)
    refs = defaultdict(set)
    for field, detail in _observations(item).items():
        group_key = (detail["source_class"], detail["provider_timestamp"],
                     detail["freshness_basis"])
        grouped[group_key][field] = detail["value"]
        grouped[group_key].update({
            "provider_timestamp": detail["provider_timestamp"],
            "capture_timestamp": detail["capture_timestamp"],
            "freshness_basis": detail["freshness_basis"],
        })
        refs[group_key].add(detail["source_ref"])
    for group_key, payload in grouped.items():
        source = group_key[0]
        evidence_v2.record_snapshot(
            store, family=item["family"], ticker=item["ticker"],
            market=item["market"], currency=item["currency"],
            settlement=item["settlement"], source_class=source,
            source_ref=";".join(sorted(refs[group_key])), observed_at=CAPTURE_AT,
            evidence=payload,
        )
    return len(grouped)


def _classify(store, item):
    if not item["ppi_identity"]:
        evaluated = rules.evaluate_family(item["family"], [], profile="OPEN", now=REPLAY_AT)
        axes = ["BLOCKED_DATA"]
        if evaluated.get("missing_dynamic"):
            axes.append("BLOCKED_DYNAMIC_DATA")
        if item["family"] not in parity.EXECUTOR_READY:
            axes.append("BLOCKED_EXECUTOR")
        return {
            "status": "BLOCKED_IDENTITY", "blocker_class": "BLOCKED_DATA",
            "blocker_axes": axes,
            "missing_contract": evaluated.get("missing_contract", []),
            "missing_dynamic": evaluated.get("missing_dynamic", []),
            "stale_dynamic": evaluated.get("stale_dynamic", []),
            "event_missing": evaluated.get("event_missing", []),
        }
    return parity.classify_instrument(
        item["family"], _records_for(store, item), profile="OPEN", now=REPLAY_AT)


def _integrated_status(store, item, classified):
    """Cross the existing Evidence -> contract -> catalog PAPER gate."""
    if classified.get("blocker_class") != "READY_PAPER_CANDIDATE":
        return classified.get("blocker_class")
    records = _records_for(store, item)
    claim = bridge.normalize_group(records, now=REPLAY_AT)
    if (claim.get("financial_contract_v17") is None
            and claim.get("paper_family_contract_v1") is None):
        return "READY_PAPER_CANDIDATE"
    primary = catalog.normalize_record({
        "ticker": item["ticker"], "type": item["family"],
        "market": item["market"], "currency": item["currency"],
    }, item["settlement"], REPLAY_AT.isoformat(), "controlled-ppi-primary")
    completed = catalog.complete_with_complement(primary, claim)
    capability = str(completed.get("capability") or "")
    return capability if capability.startswith("READY_PAPER_") else "READY_PAPER_CANDIDATE"


def _summary(rows):
    primary = Counter(row["blocker_class"] for row in rows)
    axes = Counter(axis for row in rows for axis in row.get("blocker_axes", [row["blocker_class"]]))
    return {
        "total_catalog": len(WITNESSES),
        "ppi_identities": sum(w["ppi_identity"] for w in WITNESSES),
        "candidates": sum(r["status"] != "BLOCKED_IDENTITY" for r in rows),
        "evidence_ready_candidates": sum(r["blocker_class"] == "READY_PAPER_CANDIDATE" for r in rows),
        "ready_by_instrument": sum(str(r.get("integrated_status", "")).startswith("READY_PAPER_")
                                   and r.get("integrated_status") != "READY_PAPER_CANDIDATE"
                                   for r in rows),
        "integrated_states": dict(sorted(Counter(
            r.get("integrated_status", r["blocker_class"]) for r in rows).items())),
        "primary_blockers": dict(sorted(primary.items())),
        "blocker_axes": dict(sorted(axes.items())),
        "ambiguity": 0,
        "stale_identity": sum(not w["ppi_identity"] for w in WITNESSES),
        "missing_nominal_units": sum(
            w["family"] in {"BONOS", "LETRAS", "ON"}
            and "price_quote_unit" not in _observations(w) for w in WITNESSES),
        "missing_option_contracts": sum(
            r["family"] == "OPCIONES" and r["blocker_class"] != "READY_PAPER_CANDIDATE" for r in rows),
        "missing_fci_data": sum(
            r["family"] == "FCI" and "BLOCKED_DATA" in r.get("blocker_axes", []) for r in rows),
        "missing_futures_contract_or_margin": sum(
            r["family"] == "FUTUROS" and any(x in r.get("blocker_axes", [])
            for x in ("BLOCKED_DATA", "BLOCKED_DYNAMIC_DATA")) for r in rows),
        "missing_caucion_data": sum(
            r["family"] == "CAUCIONES" and any(x in r.get("blocker_axes", [])
            for x in ("BLOCKED_DATA", "BLOCKED_DYNAMIC_DATA")) for r in rows),
    }


def _matrix(before_by_ticker, after_by_ticker):
    rows = []
    for item in WITNESSES:
        family = item["family"]
        contract = set(rules.FAMILY_CONTRACT_FIELDS[family])
        dynamic = set(rules.FAMILY_DYNAMIC_FIELDS[family])
        events = set(rules.EVENT_CONDITIONAL_FIELDS.get(family, ()))
        observed = _observations(item)
        for field in sorted(contract | dynamic | events):
            detail = observed.get(field)
            kind = "dynamic" if field in dynamic else "static"
            necessity = "event-conditional" if field in events else "required"
            profiles = "EVENT/FULL" if field in events else "OPEN/CLOSE/FULL"
            default_unit, impact = FIELD_META.get(field, (None, "contract/readiness correctness"))
            rows.append({
                **{k: item[k] for k in ("family", "ticker", "market", "currency", "settlement")},
                "source_identity": "|".join(item[k] for k in ("family", "ticker", "market", "currency", "settlement")),
                "field": field,
                "value": detail["value"] if detail else None,
                "unit": (detail.get("unit") if detail else None) or default_unit,
                "primary_source": detail["source_class"] if detail and detail["source_class"].startswith("PPI_") else None,
                "complementary_source": detail["source_class"] if detail and not detail["source_class"].startswith("PPI_") else None,
                "endpoint_mcp": detail["source_ref"] if detail and "IOL:" in detail["source_ref"] else None,
                "url_xhr": None,
                "provider_timestamp": detail["provider_timestamp"] if detail else None,
                "capture_timestamp": detail["capture_timestamp"] if detail else None,
                "freshness_basis": detail["freshness_basis"] if detail else "UNRESOLVED",
                "field_kind": kind,
                "derivable": detail["derivable"] if detail else False,
                "derivation_rule": detail["derivation_rule"] if detail else None,
                "porota_consumer": impact,
                "financial_impact_if_missing": impact,
                "profiles": profiles,
                "necessity": necessity,
                "status_before": "MISSING",
                "status_after": (
                    "DYNAMIC_WITHOUT_PROVIDER_TIMESTAMP"
                    if detail and kind == "dynamic"
                    and (detail.get("freshness_basis") != "PROVIDER_TIMESTAMP"
                         or not detail.get("provider_timestamp"))
                    else "EVIDENCED" if detail else "UNRESOLVED"
                ),
                "evidence": detail["source_ref"] if detail else item["note"] or "NO_INSTRUMENT_EVIDENCE",
                "test": "tests/test_ws_motor_11_replay.py",
                "instrument_readiness_before": before_by_ticker[item["ticker"]]["blocker_class"],
                "instrument_readiness_after": after_by_ticker[item["ticker"]].get(
                    "integrated_status", after_by_ticker[item["ticker"]]["blocker_class"]),
            })
    return rows


def replay():
    with tempfile.TemporaryDirectory(prefix="ws-motor-11-") as tmp:
        root = Path(tmp)
        snapshot = root / "controlled_snapshot.db"
        copy = root / "controlled_copy.db"
        _create_controlled_snapshot(snapshot)
        snapshot_hash_before = _sha256(snapshot)
        shutil.copy2(snapshot, copy)
        store = Store(copy)

        before = []
        for item in WITNESSES:
            row = {**item, "status": "BLOCKED_IDENTITY" if not item["ppi_identity"] else "MISSING_CONTRACT",
                   "blocker_class": "BLOCKED_DATA", "blocker_axes": ["BLOCKED_DATA"]}
            before.append(row)

        written = sum(_ingest(store, item) for item in WITNESSES)
        after = []
        for item in WITNESSES:
            classified = _classify(store, item)
            after.append({**item, **classified,
                          "integrated_status": _integrated_status(store, item, classified)})
        snapshot_hash_after = _sha256(snapshot)
        before_by_ticker = {row["ticker"]: row for row in before}
        after_by_ticker = {row["ticker"]: row for row in after}
        matrix = _matrix(before_by_ticker, after_by_ticker)
        before_summary = _summary(before)
        after_summary = _summary(after)
        before_summary["required_field_rows_evidenced"] = 0
        after_summary["required_field_rows_evidenced"] = sum(
            row["status_after"] == "EVIDENCED" for row in matrix)
        before_summary["required_field_rows_total"] = len(matrix)
        after_summary["required_field_rows_total"] = len(matrix)
        return {
            "schema": "ws-motor-12-controlled-replay-v1",
            "mode": "PAPER_SHADOW_ONLY",
            "fixture_scope": "SANITIZED_WS09_WS10_WS11_EVIDENCE_PLUS_WS12_OFFICIAL_REVALIDATION",
            "replay_at": REPLAY_AT.isoformat(),
            "productive_db_mutations": 0,
            "real_orders_sent": 0,
            "real_order_routes_used": list(REAL_ROUTES_USED),
            "original_snapshot_sha256_before": snapshot_hash_before,
            "original_snapshot_sha256_after": snapshot_hash_after,
            "original_snapshot_unchanged": snapshot_hash_before == snapshot_hash_after,
            "evidence_snapshots_written_to_copy": written,
            "before": before_summary,
            "after": after_summary,
            "instruments": [{
                "family": row["family"], "ticker": row["ticker"],
                "identity": {k: row[k] for k in ("market", "currency", "settlement")},
                "ppi_identity": row["ppi_identity"],
                "before": before_by_ticker[row["ticker"]]["blocker_class"],
                "after_evidence": row["blocker_class"],
                "after": row["integrated_status"],
                "blocker_axes": row.get("blocker_axes", [row["blocker_class"]]),
                "missing_contract": row.get("missing_contract", []),
                "missing_dynamic": row.get("missing_dynamic", []),
                "stale_dynamic": row.get("stale_dynamic", []),
                "event_missing": row.get("event_missing", []),
                "note": row["note"],
            } for row in after],
            "field_matrix": matrix,
        }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = replay()
    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
