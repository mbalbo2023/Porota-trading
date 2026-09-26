import json
from pathlib import Path

import scripts.rc6_byma_morning_pipeline as pipeline
import scripts.rc6_byma_morning_watch as watch


def _snapshot(path: Path, *, records=None, status="SCRAPED_PUBLIC_DATA"):
    records = records or [
        {"family":"OPCIONES","symbol":"GFGC7000OC","currency":"ARS","maturity":"2026-10-16"},
        {"family":"CAUCIONES","symbol":"PESOS-3D","currency":"ARS","maturity":"3"},
    ]
    payload={
        "schema":"rc6-consolidated-source-evidence-v1",
        "collected_at":"2026-09-26T11:30:00+00:00",
        "sources":[{
            "source":"BYMA","status":status,"record_count":len(records),
            "records":records,"observed_at":"2026-09-26T11:30:00+00:00",
            "scrape_method":"bymadata_public_post","errors":[],
        }],
        "decision_effect":"OBSERVE_ONLY","real_money_authorized":False,
    }
    path.write_text(json.dumps(payload),encoding="utf-8")
    return payload


def test_authority_watcher_reuses_exact_structured_scraper_snapshot(tmp_path):
    path=tmp_path/"rc6_public_sources_latest.json"
    _snapshot(path)
    first=watch._structured_byma_signature(path)
    second=watch._structured_byma_signature(path)
    assert first["status"]=="SCRAPED_PUBLIC_DATA"
    assert first["record_count"]==2
    assert first["identity_count"]==2
    assert first["identity_sha256"]==second["identity_sha256"]
    assert first["scrape_method"]=="bymadata_public_post"


def test_authority_watcher_degrades_when_shared_scraper_snapshot_is_missing(tmp_path):
    def fake_fetch(_url):
        return b"<html><body><p>Horarios de negociacion comunicado 19024</p></body></html>"
    current=watch.collect(
        fetch=fake_fetch,
        structured_snapshot=tmp_path/"missing.json",
    )
    assert any(item.startswith("OPEN_DATA:") for item in current["errors"])
    previous=dict(current)
    previous["errors"]=[]
    assert watch.compare(previous,current)["state"]=="DEGRADED"


def test_pipeline_runs_single_structured_capture_before_authority_watch(tmp_path, monkeypatch):
    order=[]

    def fake_capture(path):
        order.append("SCRAPE")
        payload=_snapshot(Path(path))
        return payload

    def fake_collect(*, structured_snapshot, **_kwargs):
        order.append("WATCH")
        assert Path(structured_snapshot).exists()
        sig=watch._structured_byma_signature(Path(structured_snapshot))
        return {
            "schema":watch.SCHEMA,
            "observed_at":"2026-09-26T11:30:05+00:00",
            "pages":{},
            "structured_byma":sig,
            "signature":"candidate",
            "errors":[],
            "decision_effect":"OBSERVE_ONLY",
            "real_money_authorized":False,
        }

    monkeypatch.setattr(pipeline,"capture",fake_capture)
    monkeypatch.setattr(pipeline,"collect",fake_collect)
    result=pipeline.run(tmp_path)

    assert order==["SCRAPE","WATCH"]
    assert result["pipeline"]["single_structured_scraper"] is True
    assert result["pipeline"]["authority_watch_reuses_structured_snapshot"] is True
    assert result["pipeline"]["byma_record_count"]==2
    latest=json.loads((tmp_path/"byma_morning_watch_latest.json").read_text())
    assert latest["structured_byma"]["record_count"]==2
    assert latest["real_money_authorized"] is False


def test_systemd_service_executes_unified_pipeline_not_second_scraper():
    service=Path("systemd/porota-byma-morning-watch-rc6.service").read_text(encoding="utf-8")
    assert "rc6_byma_morning_pipeline.py" in service
    assert "rc6_byma_morning_watch.py" not in service
    assert "rc6_public_source_capture.py" not in service


def test_pipeline_and_watcher_do_not_call_real_routes():
    pipeline_source=Path("scripts/rc6_byma_morning_pipeline.py").read_text(encoding="utf-8")
    watch_source=Path("scripts/rc6_byma_morning_watch.py").read_text(encoding="utf-8")
    for marker in ("place_order(", "cancel_order(", "place_caucion(", "subscribe_fci(", "redeem_fci("):
        assert marker not in pipeline_source
        assert marker not in watch_source
