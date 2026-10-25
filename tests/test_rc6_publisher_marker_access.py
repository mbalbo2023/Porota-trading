"""Exercise both real deploy marker writers under the publisher's file contract."""

import grp
import json
import os
import re
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import yaml


WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/porota-deploy-v2-promote.yml"


def marker_writers(source):
    steps = yaml.safe_load(source)["jobs"]["promote"]["steps"]
    snippets = [
        fragment for step in steps for fragment in
        re.findall(r"<<'PY'\n(.*?)\nPY(?:\n|$)", step.get("run", ""), re.S)
        if "tempfile.mkstemp(prefix='.CURRENT_STATE_V2.'" in fragment
        or 'tempfile.mkstemp(prefix=".CURRENT_STATE_V2."' in fragment
    ]
    assert len(snippets) == 2
    return snippets


def test_provisional_and_final_marker_are_atomic_and_readable_by_publisher(tmp_path, monkeypatch):
    snippets = marker_writers(WORKFLOW.read_text())
    expected_gid = os.getgid()
    observed = []
    real_replace = os.replace

    def group(name):
        assert name == "porotaadmin"
        return SimpleNamespace(gr_gid=expected_gid)

    def record_chown(fd, uid, gid):
        assert os.fstat(fd).st_mode & 0o777 == 0o600
        observed.append((uid, gid))

    def checked_replace(source, dest):
        assert observed, "ATOMIC_PUBLISHER_GROUP_WAS_NOT_SET"
        assert observed[-1] == (0, expected_gid)
        assert stat.S_IMODE(os.stat(source).st_mode) == 0o640
        assert os.stat(source).st_uid == os.getuid()
        return real_replace(source, dest)

    monkeypatch.setattr(grp, "getgrnam", group)
    monkeypatch.setattr(os, "fchown", record_chown)
    monkeypatch.setattr(os, "replace", checked_replace)
    marker = tmp_path / "CURRENT_STATE_V2.json"
    namespace = {"__name__": "__main__", "__file__": str(marker)}
    monkeypatch.setattr("sys.argv", ["-", str(marker), "a" * 40, "b" * 40, "sha256:" + "c" * 64, "PAPER_IDLE"])
    exec(compile(snippets[0], "provisional-marker-writer", "exec"), namespace)
    provisional = json.loads(marker.read_text())
    assert provisional["validation_status"] == "DEPLOYED_VALIDATION_PENDING"
    assert provisional["real_order_routes"] == "NOT_CALLED"
    assert stat.S_IMODE(marker.stat().st_mode) == 0o640

    # The final snippet's preceding checks touch Docker and native receipts.
    # Exercise its exact atomic write after a valid in-memory acceptance.
    final = snippets[1]
    tail = final[final.index("state['native_acceptance_mode'] = mode"):]
    context = {
        "grp": grp, "json": json, "os": os, "tempfile": tempfile,
        "datetime": datetime, "timezone": timezone, "root": tmp_path,
        "path": marker, "state": provisional, "mode": "CLOSED_IDLE",
    }
    exec(compile(tail, "final-marker-writer", "exec"), context)
    accepted = json.loads(marker.read_text())
    assert accepted["validation_status"] == "VALIDATED_RUNTIME"
    assert accepted["source_freshness_renewed"] is False
    assert accepted["real_orders_sent"] == 0
    assert len(observed) == 2
    assert stat.S_IMODE(marker.stat().st_mode) == 0o640
