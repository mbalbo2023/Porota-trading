from rc4_health_observability import explain


def test_fresh_green_is_explained():
    row=explain(state="VERDE", checked_at="2026-09-03T16:00:00Z",
                cadence_seconds=300, now="2026-09-03T16:04:00Z")
    assert row.visual_state == "GREEN"
    assert row.cause == "FRESH_OK"


def test_stale_green_becomes_explained_yellow():
    row=explain(state="VERDE", checked_at="2026-09-03T15:00:00Z",
                cadence_seconds=300, now="2026-09-03T16:00:00Z")
    assert row.visual_state == "YELLOW"
    assert row.cause == "EVIDENCIA_RETRASADA"
    assert row.age_seconds == 3600


def test_policy_off_is_not_a_delay():
    row=explain(state="NO_APLICA", checked_at="2026-09-03T10:00:00Z",
                cadence_seconds=43200, now="2026-09-03T16:00:00Z",
                enabled_by_policy=False)
    assert row.visual_state == "GRAY"
    assert row.cause == "DESHABILITADO_POR_POLITICA"


def test_missing_evidence_is_gray_not_fake_yellow():
    row=explain(state="SIN_REGISTRO", cadence_seconds=300,
                now="2026-09-03T16:00:00Z")
    assert row.visual_state == "GRAY"
    assert row.cause == "ESPERANDO_PRIMERA_MUESTRA"


def test_real_failure_stays_red_even_when_fresh():
    row=explain(state="ERROR", checked_at="2026-09-03T15:59:30Z",
                cadence_seconds=300, now="2026-09-03T16:00:00Z",
                detail="timeout")
    assert row.visual_state == "RED"
    assert row.cause == "FALLA_COMPROBADA"
