import json
from pathlib import Path

import scripts.rc6_byma_morning_watch as watch


def page(numbers=("19024",), extra=""):
    nums=" ".join(f"Nro Comunicado {n}" for n in numbers)
    return f"""<html><body><nav>Presentación de Resultados 2T 2026</nav>
    <h1>Horarios de negociación y liquidación</h1>
    <p>{nums}</p><p>Series de Opciones {extra}</p>
    <a href="/horarios-v1.pdf">Tabla de horarios PDF</a>
    <footer>Productos Calendario bursátil</footer>
    </body></html>""".encode()


def test_page_signature_extracts_only_stable_decision_relevant_content():
    first=watch._stable_page_signature(page(("19024","19017"),"Alta YPF"))
    second=watch._stable_page_signature(page(("19024","19017"),"Alta YPF"))
    assert first["sha256"]==second["sha256"]
    assert first["communication_numbers"]==["19024","19017"]
    assert first["scope"]==watch.SIGNATURE_SCOPE


def test_shared_navigation_noise_does_not_change_authority_but_pdf_and_content_do():
    initial=page()
    nav=initial.replace(b"2T 2026",b"3T 2026")
    footer=initial.replace(b"Productos Calendario",b"Productos Horarios Calendario")
    assert watch._stable_page_signature(initial)["sha256"]==watch._stable_page_signature(nav)["sha256"]
    assert watch._stable_page_signature(initial)["sha256"]==watch._stable_page_signature(footer)["sha256"]
    pdf=initial.replace(b"/horarios-v1.pdf",b"/horarios-v2.pdf")
    assert watch._stable_page_signature(initial)["sha256"]!=watch._stable_page_signature(pdf)["sha256"]
    assert watch._stable_page_signature(initial)["sha256"]!=watch._stable_page_signature(page(extra="Nueva regla"))["sha256"]


def test_missing_decision_boundary_fails_closed():
    import pytest
    with pytest.raises(ValueError,match="BYMA_DECISION_BOUNDARY_MISSING"):
        watch._stable_page_signature(b"<html><nav>Horarios</nav></html>")


def test_calendar_change_after_old_500_character_cap_is_detected():
    base=("<h1>Calendario bursátil</h1><p>"+"Día hábil "*100+
          "20 de Octubre rueda normal</p><footer>Fin</footer>").encode()
    changed=base.replace(b"rueda normal",b"rueda cerrada")
    assert watch._stable_page_signature(base)["sha256"]!=watch._stable_page_signature(changed)["sha256"]


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


def test_critical_change_stays_pending_after_a_second_identical_collection(tmp_path):
    base={"schema":watch.SCHEMA,"observed_at":"2026-10-19T11:30:00+00:00",
          "pages":{"HOURS":{"status":"OK","sha256":"old"}},
          "structured_byma":{"status":"SCRAPED_PUBLIC_DATA","identity_count":1,"identity_sha256":"x"},
          "signature":"old","errors":[],"decision_effect":"OBSERVE_ONLY","real_money_authorized":False}
    watch.persist(tmp_path,base)
    changed=json.loads(json.dumps(base))
    changed["observed_at"]="2026-10-20T06:10:00+00:00"
    changed["pages"]["HOURS"]["sha256"]="new"
    assert watch.persist(tmp_path,changed)["state"]=="CHANGED_REVIEW_REQUIRED"
    later=watch.persist(tmp_path,dict(changed,observed_at="2026-10-20T06:20:00+00:00"))
    assert later["state"]=="NO_CHANGE"
    assert later["pending_review"]["HOURS"]["sha256"]=="new"


def test_upgrade_recovers_pending_from_legacy_changed_latest(tmp_path):
    current={"schema":watch.SCHEMA,"observed_at":"2026-10-20T06:11:00+00:00",
             "pages":{"CALENDAR":{"status":"OK","sha256":"new"}},
             "structured_byma":{},"signature":"new","errors":[]}
    legacy=dict(current,observed_at="2026-10-20T06:10:00+00:00",
                state="CHANGED_REVIEW_REQUIRED",changed_components=["CALENDAR"])
    (tmp_path/"byma_morning_watch_latest.json").write_text(json.dumps(legacy))
    result=watch.persist(tmp_path,current)
    assert result["state"]=="NO_CHANGE"
    assert result["pending_review"]["CALENDAR"]["sha256"]=="new"


def test_collect_never_turns_page_evidence_into_operational_authority(monkeypatch):
    def fake_fetch(_url):
        return page(("19024","19017"))
    current=watch.collect(fetch=fake_fetch,include_structured=False)
    assert current["decision_effect"]=="OBSERVE_ONLY"
    assert current["real_money_authorized"] is False
    assert not current["errors"]


def test_structured_open_data_failure_is_complementary_not_authority_degraded():
    previous={
        "pages":{"CALENDAR":{"status":"OK","sha256":"a","communication_numbers":[]}},
        "structured_byma":{"status":"SCRAPED_PUBLIC_DATA","identity_count":2,"identity_sha256":"x"},
    }
    current={
        "pages":{"CALENDAR":{"status":"OK","sha256":"a","communication_numbers":[]}},
        "structured_byma":{"status":"SOURCE_UNAVAILABLE","identity_count":0,"identity_sha256":"y"},
        "errors":["OPEN_DATA:BYMA_NOT_STRUCTURED","OPEN_DATA:BYMA_EMPTY"],
    }
    verdict=watch.compare(previous,current)
    assert verdict["state"]=="CHANGED_REVIEW_REQUIRED"
    assert verdict["authority_errors"]==[]
    assert verdict["complementary_errors"]==[
        "OPEN_DATA:BYMA_NOT_STRUCTURED","OPEN_DATA:BYMA_EMPTY"
    ]
    assert verdict["changed_components"]==["OPEN_DATA"]


def test_official_page_error_remains_fail_closed_even_with_open_data_errors():
    previous={
        "pages":{"CALENDAR":{"status":"OK","sha256":"a","communication_numbers":[]}},
        "structured_byma":{"status":"SCRAPED_PUBLIC_DATA","identity_count":2,"identity_sha256":"x"},
    }
    current={
        "pages":{"CALENDAR":{"status":"ERROR","error":"timeout"}},
        "structured_byma":{"status":"SOURCE_UNAVAILABLE","identity_count":0,"identity_sha256":"y"},
        "errors":["CALENDAR:TimeoutError","OPEN_DATA:BYMA_EMPTY"],
    }
    verdict=watch.compare(previous,current)
    assert verdict["state"]=="DEGRADED"
    assert verdict["authority_errors"]==["CALENDAR:TimeoutError"]
