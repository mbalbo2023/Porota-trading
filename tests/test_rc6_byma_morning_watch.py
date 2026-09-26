import json
from pathlib import Path

import scripts.rc6_byma_morning_watch as watch


def page(numbers=("19024",), extra=""):
    nums=" ".join(f"Nro Comunicado {n}" for n in numbers)
    return f"""<html><body>
    <h1>Horarios de negociación y liquidación</h1>
    <p>{nums}</p><p>Series de Opciones {extra}</p>
    </body></html>""".encode()


def test_page_signature_extracts_only_stable_decision_relevant_content():
    first=watch._stable_page_signature(page(("19024","19017"),"Alta YPF"))
    second=watch._stable_page_signature(page(("19024","19017"),"Alta YPF"))
    assert first["sha256"]==second["sha256"]
    assert first["communication_numbers"]==["19024","19017"]


def test_compare_creates_baseline_then_detects_official_change():
    base={
        "pages":{"HOURS":{"status":"OK","sha256":"a","communication_numbers":["19024"]}},
        "structured_byma":{"status":"SCRAPED_PUBLIC_DATA","identity_count":2,"identity_sha256":"x"},
    }
    same=json.loads(json.dumps(base))
    same["errors"]=[]
    assert watch.compare(None,same)["state"]=="BASELINE_CREATED"
    assert watch.compare(base,same)["state"]=="NO_CHANGE"
    same["pages"]["HOURS"]["sha256"]="b"
    verdict=watch.compare(base,same)
    assert verdict["state"]=="CHANGED_REVIEW_REQUIRED"
    assert verdict["changed_components"]==["HOURS"]


def test_errors_degrade_without_authorizing_any_decision():
    previous={
        "pages":{"CALENDAR":{"status":"OK","sha256":"a","communication_numbers":[]}},
        "structured_byma":{"status":"SCRAPED_PUBLIC_DATA","identity_count":2,"identity_sha256":"x"},
    }
    current={
        "pages":{"CALENDAR":{"status":"ERROR","error":"timeout"}},
        "structured_byma":{"status":"SCRAPED_PUBLIC_DATA","identity_count":2,"identity_sha256":"x"},
        "errors":["CALENDAR:TimeoutError"],
    }
    assert watch.compare(previous,current)["state"]=="DEGRADED"


def test_persist_keeps_latest_and_append_only_history(tmp_path):
    current={
        "schema":watch.SCHEMA,"observed_at":"2026-09-25T11:30:00+00:00",
        "pages":{"HOURS":{"status":"OK","sha256":"a","communication_numbers":["19024"]}},
        "structured_byma":{"status":"SCRAPED_PUBLIC_DATA","identity_count":1,"identity_sha256":"z"},
        "signature":"sig","errors":[],"decision_effect":"OBSERVE_ONLY",
        "real_money_authorized":False,
    }
    first=watch.persist(tmp_path,current)
    assert first["state"]=="BASELINE_CREATED"
    second=watch.persist(tmp_path,dict(current, observed_at="2026-09-26T11:30:00+00:00"))
    assert second["state"]=="NO_CHANGE"
    assert len((tmp_path/"byma_morning_watch_history.jsonl").read_text().splitlines())==2
    latest=json.loads((tmp_path/"byma_morning_watch_latest.json").read_text())
    assert latest["decision_effect"]=="OBSERVE_ONLY"
    assert latest["real_money_authorized"] is False


def test_collect_never_turns_page_evidence_into_operational_authority(monkeypatch):
    def fake_fetch(_url):
        return page(("19024","19017"))
    current=watch.collect(fetch=fake_fetch,include_structured=False)
    assert current["decision_effect"]=="OBSERVE_ONLY"
    assert current["real_money_authorized"] is False
    assert not current["errors"]
