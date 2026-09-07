from __future__ import annotations

import fo_critical_approval_health_rc6 as health


class _Client:
    payload = {"status": "ok", "capability": "issues_only"}

    def health(self):
        return dict(self.payload)


def test_health_probe_green(monkeypatch):
    monkeypatch.setattr(health, "UnixGithubIssuesClient", _Client)
    _Client.payload = {"status": "ok", "capability": "issues_only"}
    assert health.main() == 0


def test_health_probe_rejects_wrong_capability(monkeypatch):
    monkeypatch.setattr(health, "UnixGithubIssuesClient", _Client)
    _Client.payload = {"status": "ok", "capability": "broader-than-issues"}
    assert health.main() == 1


def test_health_probe_rejects_broker_error(monkeypatch):
    monkeypatch.setattr(health, "UnixGithubIssuesClient", _Client)
    _Client.payload = {"status": "error", "capability": "issues_only"}
    assert health.main() == 1


def test_health_probe_fails_closed_on_exception(monkeypatch):
    class BrokenClient:
        def health(self):
            raise RuntimeError("broker unavailable")

    monkeypatch.setattr(health, "UnixGithubIssuesClient", BrokenClient)
    assert health.main() == 1
