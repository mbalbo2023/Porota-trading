"""Operator suspension takes precedence over even force=True and stale timers."""
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from be_paper_engine import PaperStore
import bi_operational_services as services
import rc6_host_general_backup_job as host
from rc6_backup_policy import backups_suspended
from rc6_trader_dashboard.views_system import render
from rc6_trader_dashboard.navigation import BY_KEY
from types import SimpleNamespace


def test_force_cannot_create_paper_or_history_backups(tmp_path, monkeypatch):
    assert services.backups_suspended() is True
    store = PaperStore(str(tmp_path / "observer.db"))
    monkeypatch.setattr(services, "BACKUP_DIR", tmp_path / "backup-paper")
    monkeypatch.setattr(services, "ROOT_DATA", tmp_path)
    assert services.create_backup(store, force=True) is None
    assert services.create_history_backup(store, force=True) is None
    assert not (tmp_path / "backup-paper").exists()
    assert not (tmp_path / "backups").exists()


def test_host_direct_invocation_stays_suspended(monkeypatch, capsys):
    assert host.backups_suspended() is True
    monkeypatch.setattr(host, "create_backup", lambda *_a, **_k: (_ for _ in ()).throw(
        AssertionError("BACKUP_CREATED")))
    assert host.main(now=datetime(2026, 10, 21, 4, 30,
                                  tzinfo=ZoneInfo("America/Argentina/Buenos_Aires"))) == 0
    assert json.loads(capsys.readouterr().out)["backup_capacity"]["status"] == "SUSPENDED_BY_OPERATOR"


def test_host_policy_masks_timer_and_preopen_does_not_require_it():
    policy = json.loads(Path("ops/policy/host-control-plane-reconciliation-v2.json").read_text())
    row = policy["units"]["systemd/porota-host-general-backup-rc6.timer"]
    assert (row["role"], row["install"], row["masked"], row["active"]) == (
        "QUARANTINED_TIMER", False, True, False)
    import rc6_preopen as preopen
    assert "porota-host-general-backup-rc6.timer" not in preopen.REQUIRED_TIMERS


def test_missing_or_invalid_policy_never_resumes_backup(tmp_path):
    target = tmp_path / "policy.json"
    assert backups_suspended(target) is True
    target.write_text('{"backups":{"suspended":"false"}}')
    assert backups_suspended(target) is True
    target.write_text('{"backups":{"suspended":false}}')
    assert backups_suspended(target) is False


def test_dashboard_discloses_suspension_without_claiming_deletion():
    html = render(SimpleNamespace(offset=0, filters={}), BY_KEY["sistema"], "backups")
    assert "SUSPENDED_BY_OPERATOR" in html
    assert "no acredita el borrado" in html
    assert "NO_VERIFICADO" in html
