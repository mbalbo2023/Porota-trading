"""Regresiones históricas reutilizadas para validar contratos RC6; sin red ni órdenes."""

import json
import sqlite3
from datetime import datetime, timezone


def test_limites_diarios_separan_freno_de_aperturas_y_corte_duro():
    from bw_daily_risk import DailyRisk
    from porota_mode_manager import PAPER_DEFAULTS

    broker = type("Broker", (), {"store": None})()
    risk = DailyRisk(broker, PAPER_DEFAULTS["MAX_DAILY_LOSS_PCT"],
                     soft_limit_pct=PAPER_DEFAULTS["PAPER_DAILY_SOFT_STOP_PCT"])
    assert risk.limit_pct == 2.5
    assert risk.soft_limit_pct == 1.5
    assert not risk.soft_stop_crossed({"baseline_equity": "1000000", "daily_pnl": "-14999.9999"})
    assert risk.soft_stop_crossed({"baseline_equity": "1000000", "daily_pnl": "-15000"})


def test_compatibilidad_del_codigo_proyectado_cuando_no_hay_freno_separado():
    from bw_daily_risk import DailyRisk

    broker = type("Broker", (), {"store": None})()
    risk = DailyRisk(broker, "1")
    assert risk.soft_limit_pct == risk.limit_pct
    assert not risk.soft_limit_explicit


def test_foco_usd_es_aditivo_y_sin_quitar_las_ocho_identidades():
    from bf_production_paper_observer import DEFAULT_FOCUS_SYMBOLS, configured_focus
    from porota_mode_manager import PAPER_DEFAULTS

    configured = configured_focus(PAPER_DEFAULTS["PAPER_FOCUS_SYMBOLS"])
    assert configured[:8] == DEFAULT_FOCUS_SYMBOLS
    assert ("AAPLD", "CEDEARS", "A-24HS") in configured
    assert ("AAPLC", "CEDEARS", "A-24HS") in configured


def test_expectativa_empirica_es_neta_descriptiva_y_no_vinculante():
    from ch_empirical_learning import empirical_expectancy

    sample = [
        {"status": "CLOSED", "currency": "ARS", "net_pnl": "10"},
        {"status": "CLOSED", "currency": "ARS", "net_pnl": "-5"},
        {"status": "CLOSED", "currency": "ARS", "net_pnl": "0"},
    ]
    result = empirical_expectancy(sample, minimum_sample=3)[0]
    assert result["net_total"] == "5.0000"
    assert result["empirical_expectancy"] == "1.6667"
    assert result["profit_factor"] == "2.0000"
    assert result["binding"] is False


def test_regimen_y_sector_son_alerta_observacional_sin_porton():
    from ci_operational_context import breadth_observation, sector_observation

    regime = breadth_observation([
        {"symbol": symbol, "currency": "ARS", "first_price": "100", "last_price": last}
        for symbol, last in (("A", "90"), ("B", "91"), ("C", "92"), ("D", "110"))
    ])
    assert regime["state"] == "BEARISH_BREADTH"
    assert regime["policy"] == "ALERT_ONLY" and regime["binding"] is False
    sectors = sector_observation([
        {"symbol": "A", "sector": "BANCOS"}, {"symbol": "B", "sector": "BANCOS"},
        {"symbol": "C", "sector": None},
    ])
    assert sectors["maximum_observed_concentration"] == 2
    assert sectors["limit"] is None and sectors["binding"] is False


def test_politicas_autorizadas_no_reemplazan_economia_binding():
    from porota_mode_manager import paper_settings

    settings = paper_settings({})
    assert settings["PAPER_ECONOMIC_GATE_MODE"] == "BINDING"
    assert settings["PAPER_EXPECTANCY_POLICY"] == "OBSERVATION_ONLY"
    assert settings["PAPER_MARKET_REGIME_POLICY"] == "ALERT_ONLY"
    assert settings["PAPER_SECTOR_CONCENTRATION_POLICY"] == "BINDING"


def test_publicacion_github_es_allowlist_y_elimina_detalle_sensible():
    from ops_publish_introspection_hf6 import sanitize

    clean = sanitize({
        "version": "HF6", "timestamp": "2026-09-01T20:15:00-03:00", "verdict": "WARN",
        "observer": {"mode": "PRODUCTION_PAPER", "real_orders_sent": 0, "secret": "LEAK"},
        "workers": {"supervisor": {"state": "RUNNING", "detail": "PRIVATE"}},
        "ppi_errors_1h": [{"event_type": "DATA_ERROR", "detail": "token=SECRET", "count": 3}],
        "caucion_readiness": {"state": "MISSING_CURRENT_CONTRACT_TERMS",
                               "missing_offer_requirements": ["quoted_at", "token"]},
        "positions": [{"account": "SECRET"}],
    })
    encoded = json.dumps(clean)
    assert "SECRET" not in encoded and "PRIVATE" not in encoded
    assert "positions" not in clean
    assert clean["ppi_errors_1h"] == [{"event_type": "DATA_ERROR", "count": 3}]
    assert clean["caucion_readiness"]["missing_offer_requirements"] == ["quoted_at"]


def test_menu_tecnico_consolidado_y_refresh_accesible():
    import bg_paper_dashboard as dashboard

    nav = dashboard._nav()
    assert nav.count(">Sistema<") == 1
    for old_top_level in (">Salud de APIs<", ">SRE<", ">Telegram<", ">Logs<", ">Configuración<"):
        assert old_top_level not in nav
    keys=[key for key, _ in dashboard.SYSTEM_SECTIONS]
    for required in ("introspeccion", "salud", "scheduler", "scraping", "backups",
                     "configuracion", "telegram", "logs"):
        assert required in keys
    assert "window.refreshPorota" in dashboard._document("x", "<p>x</p>")


def test_dashboard_no_enmascara_el_contador_de_ordenes_reales():
    from pathlib import Path

    source = Path("bg_paper_dashboard.py").read_text(encoding="utf-8")
    assert 'state["real_orders_sent"] = 0' not in source
    assert '_card("Órdenes reales", "0"' not in source
    assert 'real_orders = int(state.get("real_orders_sent") or 0)' in source


def test_proximo_chequeo_no_presenta_una_fecha_vencida_como_futura():
    import bg_paper_dashboard as dashboard

    assert dashboard._next_check("PPI_BACKGROUND_INGEST", "2026-01-01T00:00:00-03:00").startswith("ATRASADO")


def test_hold_fallback_es_canonico_y_ventas_tienen_rotulo_inequivoco():
    from pathlib import Path

    runtime = Path("bv_paper_runtime.py").read_text(encoding="utf-8")
    engine = Path("be_paper_engine.py").read_text(encoding="utf-8")
    dashboard = Path("bg_paper_dashboard.py").read_text(encoding="utf-8")
    assert 'PAPER_MAX_HOLD_MINUTES", "360"' in runtime
    assert 'PAPER_MAX_HOLD_MINUTES", "360"' in engine
    assert "Ventas pendientes de liquidación" in dashboard
    assert "no es una orden de venta abierta" in dashboard


def test_publicador_git_no_crea_objetos_como_root_y_no_usa_force_push():
    from pathlib import Path

    unit = Path("deploy/systemd/porota-introspection-publish.service").read_text(encoding="utf-8")
    script = Path("scripts/porota_publish_introspection_github_hf6.sh").read_text(encoding="utf-8")
    assert "User=porotaadmin" in unit and "Group=porotaadmin" in unit
    assert "pull --ff-only" in script and "push origin" in script
    assert "force" not in script.lower()
    assert "/opt/porota-trading/.git" not in script
    assert script.index('git -C "$REPOSITORY" push origin "$BRANCH"') < script.index("--prune-only")


def test_retencion_90_dias_exige_diario_valido_y_deja_auditoria(tmp_path):
    from ops_publish_introspection_hf6 import prune_hourly, record_publication_status

    source = tmp_path / "introspection"
    output = tmp_path / "publish"
    source.mkdir()
    (output / "daily").mkdir(parents=True)
    old_json = source / "porota_introspection_hf6_20260501T151500Z.json"
    old_md = old_json.with_suffix(".md")
    raw = {"timestamp": "2026-05-01T15:15:00+00:00", "version": "HF6"}
    old_json.write_text(json.dumps(raw), encoding="utf-8")
    old_md.write_text("evidencia", encoding="utf-8")
    (output / "daily" / "2026-05-01.json").write_text(json.dumps({
        "schema": "porota-introspection-daily-v1", "date": "2026-05-01",
        "snapshots": [{"timestamp": "2026-05-01T15:15:00+00:00"}],
    }), encoding="utf-8")
    protected = source / "porota_introspection_hf6_20260430T151500Z.json"
    protected.write_text(json.dumps({"timestamp": "2026-04-30T15:15:00+00:00"}), encoding="utf-8")
    ultimo = source / "ultimo_hf6.md"
    ultimo.write_text("puntero", encoding="utf-8")

    result = prune_hourly(source, output, retention_days=90,
                          now=datetime(2026, 9, 1, 21, tzinfo=timezone.utc))
    assert result["status"] == "PRUNED" and result["retention_days"] == 90
    assert not old_json.exists() and not old_md.exists()
    assert protected.exists() and ultimo.exists()
    audit = json.loads((output / "retention" / result["audit"]).read_text(encoding="utf-8"))
    assert audit["state"] == "COMPLETED" and audit["deleted_bytes"] > 0
    assert {item["file"] for item in audit["deleted_files"]} == {old_json.name, old_md.name}
    assert all(len(item["sha256"]) == 64 for item in audit["deleted_files"])
    assert "daily_snapshots" in audit["protected"]
    status_path = record_publication_status(
        output, "PUBLISHED", day="2026-09-01", retention_days=90)
    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["status"] == "PUBLISHED" and status["retention_days"] == 90


def test_introspeccion_sql_no_duplica_scalping_y_muestrea_por_moneda(tmp_path, monkeypatch):
    import ops_introspection_hf4 as ops

    data = tmp_path / "data"
    db_dir = data / "paper_v17"
    db_dir.mkdir(parents=True)
    database = db_dir / "observer_v17.db"
    stamp = datetime.now(ops.TZ).isoformat()
    with sqlite3.connect(database) as connection:
        connection.executescript("""
          CREATE TABLE observer_state(id INTEGER PRIMARY KEY,mode TEXT,process_state TEXT,
            session_state TEXT,ppi_auth TEXT,heartbeat_at TEXT,last_market_data_at TEXT,
            real_orders_sent INTEGER,http_allowed INTEGER,http_blocked INTEGER);
          CREATE TABLE paper_decisions(id INTEGER PRIMARY KEY,decided_at TEXT,reason TEXT);
          CREATE TABLE paper_events(id INTEGER PRIMARY KEY,event_at TEXT,event_type TEXT,detail TEXT);
          CREATE TABLE paper_positions(paper_id TEXT PRIMARY KEY,status TEXT,currency TEXT,
            net_pnl TEXT,opened_at TEXT,closed_at TEXT,symbol TEXT,asset_class TEXT,
            market TEXT,settlement TEXT,features_json TEXT);
          CREATE TABLE paper_fills(id INTEGER PRIMARY KEY,paper_id TEXT,side TEXT);
          CREATE TABLE trade_gate_evaluations(id INTEGER PRIMARY KEY,evaluated_at TEXT,
            decision_key TEXT,symbol TEXT,final_result TEXT,technical_gate TEXT,
            patrimonial_gate TEXT,detail_json TEXT);
          CREATE TABLE market_snapshots(id INTEGER PRIMARY KEY,observed_at TEXT,symbol TEXT,
            currency TEXT,last TEXT,last_kind TEXT,trade_at TEXT);
        """)
        connection.execute("INSERT INTO observer_state VALUES(1,?,?,?,?,?,?,?,?,?)",
                           ("PRODUCTION_PAPER", "WAITING_MARKET", "MARKET_CLOSED", "OK",
                            stamp, stamp, 0, 1, 0))
        connection.executemany("INSERT INTO paper_events VALUES(NULL,?,?,?)", (
            (stamp, "PAPER_FILLED_BUY", "fill único"),
            (stamp, "SCALPING_PAPER_FILLED_BUY", "etiqueta del mismo fill"),
        ))
        positions = []
        for currency, count in (("ARS", 101), ("USD_MEP", 3)):
            for index in range(count):
                paper_id = f"{currency}-{index}"
                positions.append((paper_id, "CLOSED", currency, "1", stamp, stamp,
                                  "TEST", "CEDEARS", "BYMA", "A-24HS", "{}"))
        connection.executemany("INSERT INTO paper_positions VALUES(?,?,?,?,?,?,?,?,?,?,?)", positions)
        connection.executemany("INSERT INTO paper_fills VALUES(NULL,?,?)",
                               ((position[0], "SELL_SIMULATED") for position in positions))
    (data / "operation_mode.json").write_text(
        json.dumps({"mode": "PRODUCTION_PAPER"}), encoding="utf-8")
    monkeypatch.setattr(ops, "DB", str(database))
    monkeypatch.setattr(ops, "OUT", data / "introspection")

    report = ops.collect()
    samples = {item["currency"]: item["samples"] for item in report["learning_expectancy"]}
    assert report["trading"]["buys_1h"] == 1
    assert samples == {"ARS": 100, "USD_MEP": 3}
    assert report["observer"]["real_orders_sent"] == 0


def test_deploy_hf6_construye_y_prueba_antes_del_corte_sin_backup_ni_red():
    from pathlib import Path

    source = Path("scripts/v17_rc3_hf6_deploy.py").read_text(encoding="utf-8")
    build = source.index('docker("build"')
    tests = source.index('"--entrypoint", "pytest"')
    cut = source.index("        activate_candidate(candidate, repo)")
    persist = source.index("        persist_source(repo, extracted, manifest)")
    assert build < tests < cut < persist
    assert '"--network", "none"' in source
    assert '"predeploy_backup": False' in source
    assert "git_mutated\": False" in source
    assert "force" not in source.lower()
    assert "docker system prune" not in source and "docker volume" not in source
