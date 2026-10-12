"""Native c27 reconstruction of AUD-468-19/U16/U26 and positive controls.

This external audit driver imports every product/helper from one complete Git
archive. It changes neither product code nor DDL. Only synthetic data/clock and
read-only client boundaries are supplied; all stores use native constructors.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
import hashlib
import json
import os
from pathlib import Path
import platform
import socket
import sqlite3
import sys
import tempfile
import traceback


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot_vector_probe(directory):
    import be_paper_engine as engine
    import bv_paper_runtime as runtime
    import cf_intraday_scalping as sc
    from be_paper_engine import PaperBroker, PaperStore, Quote
    from bm_exit_supervisor import PositionExitSupervisor

    at = datetime.fromisoformat("2026-10-05T10:46:00-03:00")
    record = {"ticker": "GGAL", "instrument_type": "ACCIONES", "market": "BYMA",
        "currency": "ARS", "settlement": "A-24HS", "capability": "READY_PAPER_SPOT", "status": "AVAILABLE"}
    # Original #468 L_SC_MUTABLE_NO_VERSION values; native real PaperStore
    # replaces the original audit's miniature hand-created SQL schema.
    mutable = PaperStore(str(directory / "mutable.sqlite"))
    sc.init_schema(mutable)
    original_now = datetime(2026, 10, 2, 16, tzinfo=timezone.utc)
    event_at = (original_now - timedelta(seconds=60)).isoformat(timespec="microseconds")
    first_received = (original_now - timedelta(seconds=30)).isoformat(timespec="microseconds")
    sc.persist_payload(mutable, record, [(event_at, D("100"), D("1"))], received_at=first_received)
    with mutable.connect() as connection:
        first = dict(connection.execute("SELECT * FROM ppi_intraday_points").fetchone())
    sc.persist_payload(mutable, record, [(event_at, D("110"), D("2"))],
                       received_at=original_now.isoformat(timespec="microseconds"))
    with mutable.connect() as connection:
        second = dict(connection.execute("SELECT * FROM ppi_intraday_points").fetchone())
        row_count = connection.execute("SELECT count(*) FROM ppi_intraday_points").fetchone()[0]

    environment = dict(os.environ)
    old_engine_now, old_runtime_now = engine.now_iso, runtime.now_iso
    clock = [at]
    def native_clock():
        clock[0] += timedelta(microseconds=1)
        return clock[0].isoformat(timespec="microseconds")
    try:
        os.environ.update({"PAPER_SCALPING_MODE": "ACTIVE_PAPER",
            "PAPER_ECONOMIC_GATE_MODE": "BINDING", "PAPER_AI_GATE_MODE": "OFF",
            "PAPER_SECTOR_CONCENTRATION_POLICY": "OBSERVATION_ONLY",
            "POROTA_DYNAMIC_CAPACITY_MODE": "OFF", "PAPER_INITIAL_CAPITAL_ARS": "1000000",
            "PAPER_SCALPING_RISK_PER_TRADE": ".001", "PAPER_SCALPING_STOP_LOSS_PCT": ".02",
            "PAPER_SCALPING_TARGET_GAIN_PCT": ".10", "PAPER_SCALPING_MAX_OPEN_POSITIONS": "1"})
        engine.now_iso = runtime.now_iso = native_clock
        store = PaperStore(str(directory / "binding_scalping.sqlite"))
        sc.init_schema(store)
        start = at.replace(hour=10, minute=30, second=0, microsecond=0)
        def payload(count):
            return [{"date": (start+timedelta(minutes=i)).isoformat(),
                "price": str(D("100")+D(i)/D("4")), "volume": str(20 if i%2==0 else 10)}
                for i in range(count)]
        for count, received in ((15, at-timedelta(minutes=1)), (16, at)):
            sc.persist_payload(store, record, sc.normalize_payload(payload(count), received_at=received.isoformat()),
                               received_at=received.isoformat())
        q = Quote("GGAL", "ACCIONES", "A-24HS", D("103.75"), D("103.75"), D("103.80"),
            D("1000"), D("1000"), at.isoformat(), currency="ARS", market="BYMA",
            metadata_source="PPI_PRIMARY_SYNTHETIC_AUD19", book_at=at.isoformat(),
            trade_at=at.isoformat(), last_kind="TRADE")
        store.add_quote(q)
        native_broker = runtime.broker_from_environment(store, risk_pct=".001",
            stop_loss_pct=".02", target_gain_pct=".10")
        canonical_economics = native_broker._economic_diagnostics(q)
        assert canonical_economics["passed"] is True, "AUD19_CANONICAL_BINDING_POSITIVE_CONTROL_FAILED"
        supervisor = PositionExitSupervisor(native_broker, clock_fn=native_clock,
            session_policy=native_broker.session_policy, max_hold_minutes=360)
        supervisor.tick()
        with store.connect() as connection:
            connection.execute("INSERT OR REPLACE INTO paper_exit_reader_state VALUES(1,?,'READY','synthetic')",
                               (native_clock(),))
        candidate = sc.evaluate_candidate(store, record, at=at.isoformat())
        assert candidate == "BUY_CANDIDATE", "AUD19_NATIVE_CANDIDATE_POSITIVE_CONTROL_FAILED:"+candidate
        promoted = sc.promote_paper_candidate(store, record, at=at.isoformat())
        assert promoted == "OPENED_SIMULATED", "AUD19_NATIVE_PROMOTION_POSITIVE_CONTROL_FAILED:"+promoted
        with store.connect() as connection:
            position = dict(connection.execute("SELECT paper_id,status,entry_price,entry_cost,features_json FROM paper_positions").fetchone())
            gate = dict(connection.execute("SELECT decision_key,paper_id,final_result FROM trade_gate_evaluations WHERE paper_id=?",
                                           (position["paper_id"],)).fetchone())
            snapshot = dict(connection.execute("SELECT * FROM decision_evidence_snapshots WHERE decision_key=?",
                                               (gate["decision_key"],)).fetchone())
            observed_snapshot = dict(connection.execute("SELECT * FROM decision_evidence_snapshots WHERE decision_key LIKE 'scalping-native:%'").fetchone())
            point_count = connection.execute("SELECT count(*) FROM ppi_intraday_points").fetchone()[0]
        frozen = json.loads(snapshot["payload_json"])
        observed = json.loads(observed_snapshot["payload_json"])
        assert frozen["decision"]["paper_id"] == position["paper_id"] == gate["paper_id"]
        frozen_vector = frozen["inputs_used"].get("entry_signal_inputs")
        assert sha_bytes(snapshot["payload_json"].encode()) == snapshot["payload_sha256"]

        # Positive control: c27 already captures the exact main native vector.
        # Its preserved capability must not be mislabeled a universal failure.
        main_store = PaperStore(str(directory / "binding_main.sqlite"))
        main_broker = PaperBroker(main_store, initial_cash="1000000", risk_pct=".001",
            participation=".1", max_position_pct=".25", max_total_exposure_pct=".60",
            clock_fn=native_clock, session_policy=None, require_supervisor=False,
            ai_mode="OFF", economics_mode="BINDING", stop_loss_pct=".02", target_gain_pct=".10")
        values = ("100", "101", "100.5", "102", "101.5", "103", "103.5", "104")
        for i, value in enumerate(values):
            when = at+timedelta(minutes=i+1)
            clock[0] = when
            book = Quote("MAIN", "ACCIONES", "A-24HS", D(value), D(value), D(value)+D(".05"),
                D("1000"), D("1000"), when.isoformat(), currency="ARS", market="BYMA",
                metadata_source="PPI_PRIMARY_SYNTHETIC_AUD19_CONTROL", book_at=when.isoformat(),
                trade_at=when.isoformat(), last_kind="TRADE")
            main_store.add_quote(book)
            if i == len(values)-1:
                assert main_broker._economic_diagnostics(book)["passed"] is True
                main_broker.on_quote(book)
        with main_store.connect() as connection:
            main_position = connection.execute("SELECT paper_id FROM paper_positions").fetchone()
            main_snapshot = dict(connection.execute("SELECT * FROM decision_evidence_snapshots").fetchone())
        assert main_position, "AUD19_MAIN_NATIVE_FILL_POSITIVE_CONTROL_FAILED"
        main_payload = json.loads(main_snapshot["payload_json"])
        main_vector = main_payload["inputs_used"]["entry_signal_inputs"]
        assert main_vector["price_sample_status"] == "NATIVE_EXACT_VECTOR"
        assert [row["price"] for row in main_vector["price_samples"]] == list(values)
        return {"mutable_store": {"first": first, "second": second, "row_count": row_count,
                    "prior_price_lost_from_canonical_store": second["price"]=="110" and row_count==1},
            "native_scalping": {"candidate": candidate, "promotion": promoted,
                "canonical_binding_economics": canonical_economics,
                "native_runtime_broker_factory_used": True, "point_count": point_count,
                "position": {k:v for k,v in position.items() if k!="features_json"},
                "gate": gate, "snapshot": snapshot, "frozen_payload": frozen,
                "complete_price_vector_available": isinstance(frozen_vector, dict) and bool(frozen_vector.get("price_samples")),
                "missing_native_clocks": [name for name in ("signal_at","decision_at","intent_at","entry_fill_committed_at") if not frozen.get(name)]},
            "separate_observed_signal_control": {"snapshot_key":observed_snapshot["decision_key"],
                "payload_sha256":observed_snapshot["payload_sha256"],"paper_id":observed["decision"]["paper_id"],
                "price_samples":len(observed["inputs_used"]["entry_signal_inputs"]["price_samples"]),
                "explicit_native_key_in_filled_position":json.loads(position["features_json"]).get("native_decision_key")},
            "preserved_main_control": {"paper_id": main_position[0], "snapshot_sha256": main_snapshot["payload_sha256"],
                "native_vector_status": main_vector["price_sample_status"], "native_vector_prices": [row["price"] for row in main_vector["price_samples"]]},
            "product_red": not isinstance(frozen_vector, dict) or not frozen_vector.get("price_samples"),
            "boundary": "CANONICAL_BINDING_PASSES; REAL_NATIVE_CF_PROMOTE_FILL_LACKS_EXACT_VECTOR; MAIN_VECTOR_CAPTURE_IS_PRESERVED"}
    finally:
        engine.now_iso, runtime.now_iso = old_engine_now, old_runtime_now
        os.environ.clear(); os.environ.update(environment)


def sha_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sanitize_probe(directory):
    import iol_shadow_collector_rc6 as collector
    from rc6_shadow_runtime.persistence import read_committed_generation
    from rc6_shadow_runtime.worker import ShadowRuntime
    from tests.test_rc6_shadow_runtime_wiring import PRE, make_store
    marker = "SYNTHETIC_CREDENTIAL_MARKER_469"
    market = directory / "market"
    class FailedClient:
        def call(self, *_args):
            raise RuntimeError("token="+marker)
    policy = collector.CollectionPolicy(retry_attempts=0)
    payload = collector.run_batch(["S1"], FailedClient(), root=market, policy=policy,
        governor=collector.RateGovernor(policy, sleep=lambda _seconds: None), now=lambda: PRE)
    files = {p.name:p.read_text() for p in market.glob("*.json")}
    store, _ = make_store(directory, count=1)
    worker = ShadowRuntime(store.path, evidence_root=directory/"shadow", source_roots=[market])
    report = worker.tick(PRE)
    committed = read_committed_generation(worker.root)
    propagated = marker in json.dumps(committed["report"],sort_keys=True)
    source = next(item for item in report["source_reports"] if item["source"]=="IOL")
    # Same native caller with a marker-free synthetic valid source is control.
    clean_market = directory/"clean-market"; clean_market.mkdir()
    clean_row = {"ticker":"S1","instrument_type":"ACCIONES","market":"BYMA",
        "currency":"ARS","settlement":"A-24HS","price":100,"price_unit":"PER_SHARE",
        "timestamp":PRE.isoformat(),"received_at":PRE.isoformat(),"source_path":"fixture/iol/S1","state":"READY"}
    (clean_market/"iol_shadow_latest.json").write_text(json.dumps({"symbols":[clean_row],"errors":[],"status":"SHADOW_EVIDENCE"}))
    control = ShadowRuntime(store.path,evidence_root=directory/"clean-shadow",source_roots=[clean_market]).tick(PRE)
    clean_committed = read_committed_generation(directory/"clean-shadow")
    assert marker not in json.dumps(clean_committed,sort_keys=True)
    return {"marker":"SYNTHETIC_ONLY_NOT_A_REAL_SECRET","collector_cache_checkpoint_marker_files":[name for name,body in files.items() if marker in body],
        "collector_payload_contains_marker":marker in json.dumps(payload),
        "raw_marker_in_source_report":marker in json.dumps(source),"raw_marker_in_committed_report":propagated,
        "source_report":source,"committed_generation_id":committed["pointer"]["generation_id"],
        "clean_full_tick_control_committed":bool(clean_committed["report"]),
        "real_orders_sent":report["real_orders_sent"],"provider_requests":report["provider_requests"],
        "product_red":propagated and any(marker in body for body in files.values()),
        "boundary":"REAL_NATIVE_COLLECTOR_AND_WORKER_WITH_FAILED_READONLY_CLIENT; SYNTHETIC_MARKER_ONLY; NO_ENTRY_AUTHORITY"}


def duplicate_probe(directory):
    from rc6_dynamic_universe.common import digest
    from rc6_dynamic_universe.sources import audit_sources, source_observations
    from rc6_shadow_runtime.persistence import EvidenceFiles, read_committed_generation
    at="2026-10-05T13:20:10+00:00"
    row={"ticker":"S1","instrument_type":"ACCIONES","market":"BYMA","currency":"ARS",
        "settlement":"A-24HS","price":100,"price_unit":"PER_SHARE","timestamp":"2026-10-05T13:20:00+00:00",
        "received_at":"2026-10-05T13:20:05+00:00","source_path":"fixture/S1"}
    report=source_observations({"records":[row]},source="PPI_API",as_of=at)
    single=audit_sources(reports=[report],as_of=at)
    exact=[report,deepcopy(report)]
    duplicate=audit_sources(reports=exact,as_of=at)
    conflicting=deepcopy(report);conflicting["observations"][0]["fields"]["price"]=101
    conflict=audit_sources(reports=[report,conflicting],as_of=at)
    duplicate_row=deepcopy(report);duplicate_row["observations"]*=2
    duplicate_row["counts"]={"seen":2,"useful":2,"rejected":0}
    row_audit=audit_sources(reports=[duplicate_row],as_of=at)
    base={"as_of":at,"mode":"SHADOW","status":"SHADOW_OBSERVING","real_orders_sent":0,"real_routes":"NOT_CALLED"}
    with EvidenceFiles(directory/"duplicate-evidence") as files:
        published=files.commit_generation({**base,"source_reports":exact,"source_audit":duplicate},
            dict(base),dict(base),source_watermark={"as_of":at,"source_identity":"synthetic"},configuration_fingerprint="original-u26-reconstruction")
    committed=read_committed_generation(directory/"duplicate-evidence")
    assert single["source_report_count"]==1
    return {"single_positive_control_count":single["source_report_count"],"input_report_digest":digest(report),
        "duplicate_exact_report_digests":[digest(item) for item in exact],
        "duplicate_exact_count":duplicate["source_report_count"],"duplicate_conflicting_count":conflict["source_report_count"],
        "duplicate_observation_report_count":row_audit["source_report_count"],
        "duplicate_observation_seen_count":duplicate_row["counts"]["seen"],
        "native_committed_duplicate_count":committed["report"]["source_audit"]["source_report_count"],
        "native_committed_report_list_count":len(committed["report"]["source_reports"]),
        "publication_generation_id":published["pointer"]["generation_id"],
        "product_red":duplicate["source_report_count"]==2,
        "natural_caller_producing_duplicates_demonstrated":False,
        "boundary":"NATIVE_NORMALIZER_AUDIT_AND_PUBLICATION_BOUNDARY_WITH_ADVERSARIAL_REPORT_LIST; NOT_A_NATURAL_DUPLICATING_WORKER_CLAIM"}


def main():
    parser=argparse.ArgumentParser();parser.add_argument("--source-index",type=Path,required=True);parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args();index=json.loads(args.source_index.read_bytes());root=Path(index["extracted_root"]).resolve(strict=True)
    assert not args.output.resolve().is_relative_to(root)
    expected=index["source_file_hashes"];before={name:sha(root/name) for name in expected};assert before==expected
    sys.path[:]=[str(root)]+[p for p in sys.path if not p.startswith("/workspace/porota_rc6_")]
    os.chdir(root);sys.dont_write_bytecode=True
    os.environ["PYTHONDONTWRITEBYTECODE"]="1";os.environ.pop("POROTA_RUNTIME_SCHEMA_READY",None)
    attempts=[]
    def deny(*_args,**_kwargs):
        attempts.append("NETWORK_BLOCKED");raise RuntimeError("ORIGINAL_SNAPSHOT_SOURCE_PROBE_NETWORK_FORBIDDEN")
    socket.socket.connect=socket.socket.connect_ex=socket.create_connection=socket.getaddrinfo=deny
    started=datetime.now(timezone.utc).isoformat();results={}
    with tempfile.TemporaryDirectory(prefix="rc6-original-snapshot-sources-") as td:
        scratch=Path(td)
        os.environ.update({"DATA_DIR":td,"HIST_DB_PATH":str(scratch/"absent-history.sqlite"),
            "POROTA_IOL_SHADOW_CACHE_PATH":str(scratch/"absent-iol-cache.json"),
            "PAPER_SECTOR_CONCENTRATION_POLICY":"OBSERVATION_ONLY"})
        for name,callback in (("AUD-468-19",snapshot_vector_probe),("U16",sanitize_probe),("U26",duplicate_probe)):
            directory=scratch/name;directory.mkdir()
            try:results[name]={"execution":"COMPLETED","result":callback(directory)}
            except Exception as error:results[name]={"execution":"HARNESS_ERROR_NOT_PRODUCT_RED","exception":repr(error),"traceback":traceback.format_exc()}
    imported,alien,origins={},{},{}
    modules={name[:-3].replace("/",".").removesuffix(".__init__") for name in expected if name.endswith(".py")}
    for name,module in list(sys.modules.items()):
        filename=getattr(module,"__file__",None)
        if not filename:continue
        path=Path(filename).resolve()
        if not path.is_file():continue
        origins[name]=str(path)
        if path.is_relative_to(root):
            relative=str(path.relative_to(root));imported[name]={"path":relative,"sha256":sha(path)}
            if relative not in expected or imported[name]["sha256"]!=expected[relative]:alien[name]=str(path)
        elif name in modules or str(path).startswith("/workspace/porota_rc6_"):
            if not(name in {"__main__","__mp_main__"} and path==Path(__file__).resolve()):alien[name]=str(path)
    after={name:sha(root/name) for name in expected};valid=before==after and not alien and not attempts
    receipt={"schema":"rc6.original-snapshot-source-archive-replay.v1","status":"VALID_SOURCE_REPLAY" if valid else "INVALID_SOURCE_OR_NETWORK_REPLAY",
        "source_sha":index["source_sha"],"source_tree":index["source_tree"],"archive_sha256":index["archive_sha256"],"source_index_sha256":sha(args.source_index),
        "probe_sha256":sha(Path(__file__)),"source_files_before":before,"source_files_after":after,"source_unchanged":before==after,
        "imported_repo_modules":imported,"alien_repo_modules":alien,"all_imported_file_origins":origins,"network_attempts":len(attempts),
        "started_at":started,"finished_at":datetime.now(timezone.utc).isoformat(),"python":platform.python_version(),"sqlite":sqlite3.sqlite_version,
        "results":results,"scope":"NATIVE_ORIGINAL_APIS_ON_COMPLETE_C27_ARCHIVE; SYNTHETIC_TEMPORARY_INPUTS_AND_READONLY_BOUNDARIES; NO_PRODUCT_OR_DDL_EDITS",
        "final_frozen_execution":"PENDING_PARENT_FREEZE","real_orders_sent":0,"release_artifact_verified":False,
        "external_20_sessions_master":"EXTERNAL_NO_VERIFICADO","economic_edge":"EDGE_NO_DEMOSTRADO"}
    args.output.write_text(json.dumps(receipt,indent=2,sort_keys=True,default=str)+"\n")
    print(json.dumps({"status":receipt["status"],"repo_imports":len(imported),"alien_imports":len(alien),"network_attempts":len(attempts),
        "cases":{k:{"execution":v["execution"],"product_red":v.get("result",{}).get("product_red")} for k,v in results.items()}}))
    return 0 if valid and all(v["execution"]=="COMPLETED" for v in results.values()) else 2


if __name__=="__main__":
    raise SystemExit(main())
