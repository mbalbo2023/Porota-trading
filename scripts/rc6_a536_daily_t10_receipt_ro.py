#!/usr/bin/env python3
"""A536: inspect an EXISTING, root-owned RC6 T-10 PAPER receipt, never issue one.

The only successful classification is OBSERVED_ONLY. It is neither an
admission decision nor evidence of a PAPER fill. No DB, broker, provider or
host write is performed; the script is streamed to the pinned droplet by a
main-only manual GitHub Action and checks that host's current ART session.
"""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
import re
import stat
import subprocess
import sys
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ART = ZoneInfo("America/Argentina/Buenos_Aires")
SHA = re.compile(r"[0-9a-f]{40}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
IMAGE = re.compile(r"sha256:[0-9a-f]{64}\Z")
PRODUCT = Path("/opt/porota-trading")
DEPLOY = PRODUCT / "data/deploy"
OPERATOR = Path("/opt/porota-operator")
GATE_PATH = OPERATOR / "paper-preopen-v2.json"
HELPER = PRODUCT / "scripts/rc6_operator_paper_rearm.py"
MANIFEST = DEPLOY / "porota-deploy-bundle-v2-manifest.json"
SCHEMA = "rc6.a536.daily-t10-existing-receipt-ro.v1"
EXPECTED_DROPLET = ("594077619", "nyc1")
METADATA = "http://169.254.169.254/metadata/v1/"
REQUEST_KEYS = frozenset({"trade_day_ar", "expected_product_sha", "source_commit",
                          "source_sha256", "requested_at_ar"})
RESULT_KEYS = frozenset({"schema", "status", "reason", "trade_day_ar",
                         "observed_at_ar", "installed_sha", "installed_tree_sha",
                         "image_id", "source_commit", "source_sha256",
                         "read_only", "entry_authority", "host_mutations", "database_opened",
                         "provider_calls", "ppi_watch_touched",
                         "real_orders_sent_by_action"})
ALLOWED_REASONS = frozenset({
    "CHECK_NOT_EXECUTED", "CLOCK_UNVERIFIED", "CLOCK_OFFSET_UNVERIFIED",
    "CURRENT_ART_T10_CHECK_WINDOW_CLOSED", "REQUEST_SHAPE_INVALID",
    "SHA_INVALID", "SOURCE_DIGEST_INVALID", "TRADE_DAY_MISMATCH",
    "REQUEST_CLOCK_INVALID", "REQUEST_EXPIRED_OR_FUTURE",
    "OPERATOR_DIRECTORY_UNTRUSTED", "RECEIPT_OR_RELEASE_FILE_UNTRUSTED",
    "RECEIPT_OR_RELEASE_CHANGED", "DOCKER_READONLY_INSPECT_UNVERIFIED",
    "CONTAINER_STATE_INVALID", "CONTAINER_ENV_INVALID",
    "CONTAINER_ENV_AMBIGUOUS", "INSTALLED_PRODUCT_RELEASE_NOT_VERIFIED_PAPER0",
    "INSTALLED_HELPER_MANIFEST_UNVERIFIED",
    "INSTALLED_HELPER_MANIFEST_ENTRY_INVALID",
    "INSTALLED_HELPER_FILE_UNTRUSTED", "INSTALLED_HELPER_HASH_MISMATCH",
    "INSTALLED_HELPER_IMPORT_UNVERIFIED",
    "EXISTING_T10_GO_RECEIPT_NOT_VERIFIED", "OPERATOR_INTENT_NOT_VERIFIED",
    "HOST_READONLY_PROBE_UNVERIFIED", "DUPLICATE_JSON_KEY", "NONFINITE_JSON",
    "JSON_OBJECT_REQUIRED", "REQUEST_SIZE_INVALID", "EXISTING_GO_RECEIPT_MATCHED",
})


class ProbeBlocked(ValueError):
    pass


def _distinct_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProbeBlocked("DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def _json(raw):
    def _reject_constant(_value):
        raise ProbeBlocked("NONFINITE_JSON")
    value = json.loads(raw, object_pairs_hook=_distinct_pairs,
                       parse_constant=_reject_constant)
    if type(value) is not dict:
        raise ProbeBlocked("JSON_OBJECT_REQUIRED")
    return value


def _art(value):
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ProbeBlocked("CLOCK_UNVERIFIED")
    local = value.astimezone(ART)
    if local.utcoffset().total_seconds() != -10800:
        raise ProbeBlocked("CLOCK_OFFSET_UNVERIFIED")
    return local


def _window(now):
    local = _art(now)
    minute = local.hour * 60 + local.minute
    # Observe only AFTER the 10:20-10:29 issuance window, before late sessions.
    # The host checks this independently of the runner clock.
    if local.weekday() >= 5 or not 630 <= minute < 645:
        raise ProbeBlocked("CURRENT_ART_T10_CHECK_WINDOW_CLOSED")
    return local


def request_from_environment(*, now):
    local = _window(now)
    return {"trade_day_ar": local.date().isoformat(),
            "expected_product_sha": os.environ["RC6_EXPECTED_PRODUCT_SHA"],
            "source_commit": os.environ["GITHUB_SHA"],
            "source_sha256": os.environ["RC6_SOURCE_SHA256"],
            "requested_at_ar": local.isoformat(timespec="seconds")}


def validate_request(request, now):
    local = _window(now)
    if type(request) is not dict or set(request) != REQUEST_KEYS:
        raise ProbeBlocked("REQUEST_SHAPE_INVALID")
    if any(not isinstance(request[key], str) or not SHA.fullmatch(request[key])
           for key in ("expected_product_sha", "source_commit")):
        raise ProbeBlocked("SHA_INVALID")
    if not isinstance(request["source_sha256"], str) or not SHA256.fullmatch(request["source_sha256"]):
        raise ProbeBlocked("SOURCE_DIGEST_INVALID")
    if request["trade_day_ar"] != local.date().isoformat():
        raise ProbeBlocked("TRADE_DAY_MISMATCH")
    try:
        issuance = datetime.fromisoformat(request["requested_at_ar"])
    except (TypeError, ValueError):
        raise ProbeBlocked("REQUEST_CLOCK_INVALID") from None
    if issuance.tzinfo is None or issuance.utcoffset() != local.utcoffset():
        raise ProbeBlocked("REQUEST_CLOCK_INVALID")
    age = (local - issuance.astimezone(ART)).total_seconds()
    if issuance.date() != local.date() or not 0 <= age <= 150:
        raise ProbeBlocked("REQUEST_EXPIRED_OR_FUTURE")
    return local


def _read_json(path, maximum, *, root_owned=False, root_directory=False):
    if root_directory:
        directory = os.lstat(path.parent)
        if (not stat.S_ISDIR(directory.st_mode) or directory.st_uid != 0
                or directory.st_mode & 0o022):
            raise ProbeBlocked("OPERATOR_DIRECTORY_UNTRUSTED")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        st = os.fstat(fd)
        if (not stat.S_ISREG(st.st_mode) or st.st_nlink != 1
                or not 0 < st.st_size <= maximum
                or (root_owned and (st.st_uid != 0 or st.st_mode & 0o022))):
            raise ProbeBlocked("RECEIPT_OR_RELEASE_FILE_UNTRUSTED")
        raw = os.read(fd, maximum + 1)
        if len(raw) != st.st_size:
            raise ProbeBlocked("RECEIPT_OR_RELEASE_CHANGED")
        return _json(raw)
    finally:
        os.close(fd)


def _command(argv):
    try:
        result = subprocess.run(argv, text=True, capture_output=True, timeout=3,
                                check=True)
        return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        raise ProbeBlocked("DOCKER_READONLY_INSPECT_UNVERIFIED") from None


def _container(name):
    parts = _command(["docker", "inspect", "--format",
                      "{{.State.Running}}|{{.Image}}|{{.HostConfig.ReadonlyRootfs}}",
                      name]).split("|")
    if len(parts) != 3 or parts[0] not in {"true", "false"} or parts[2] not in {"true", "false"}:
        raise ProbeBlocked("CONTAINER_STATE_INVALID")
    values = {"running": parts[0] == "true", "image": parts[1],
              "readonly_rootfs": parts[2] == "true"}
    if name == "porota_production_observer":
        raw = json.loads(_command(["docker", "inspect", "--format", "{{json .Config.Env}}", name]))
        if type(raw) is not list:
            raise ProbeBlocked("CONTAINER_ENV_INVALID")
        picked = {}
        for item in raw:
            if isinstance(item, str) and "=" in item:
                key, value = item.split("=", 1)
                if key in {"POROTA_BUILD_SHA", "POROTA_CANDIDATE_TREE_SHA"}:
                    if key in picked:
                        raise ProbeBlocked("CONTAINER_ENV_AMBIGUOUS")
                    picked[key] = value
        values["build_sha"] = picked.get("POROTA_BUILD_SHA")
        values["tree_sha"] = picked.get("POROTA_CANDIDATE_TREE_SHA")
    return values


def verify_release(expected_sha):
    marker = _read_json(DEPLOY / "CURRENT_STATE_V2.json", 65536)
    frozen = _read_json(DEPLOY / "porota-frozen-candidate.json", 16384)
    observer = _container("porota_production_observer")
    dashboard = _container("porota_production_dashboard")
    tree, image = frozen.get("candidate_tree_sha"), marker.get("image_id")
    if (not SHA.fullmatch(str(tree or "")) or not IMAGE.fullmatch(str(image or ""))
            or marker.get("candidate_sha") != expected_sha
            or frozen.get("candidate_sha") != expected_sha
            or marker.get("validation_status") != "VALIDATED_RUNTIME"
            or marker.get("mode") != "PRODUCTION_PAPER"
            or type(marker.get("real_orders_sent")) is not int
            or marker["real_orders_sent"] != 0
            or marker.get("real_order_routes") != "NOT_CALLED"
            or marker.get("ppi_watch_untouched") is not True
            or frozen.get("real_order_capability_required") != "BLOCKED"
            or frozen.get("paper_mode_required") != "PRODUCTION_PAPER"
            or frozen.get("build_once") is not True
            or not observer["running"] or not dashboard["running"]
            or observer["image"] != image or dashboard["image"] != image
            or not observer["readonly_rootfs"]
            or observer["build_sha"] != expected_sha
            or observer["tree_sha"] != tree):
        raise ProbeBlocked("INSTALLED_PRODUCT_RELEASE_NOT_VERIFIED_PAPER0")
    return tree, image


def verify_installed_helper():
    """Only execute the pre-existing operator evaluator from its frozen bundle."""
    manifest = _read_json(MANIFEST, 8 * 1024 * 1024, root_owned=True)
    rows = manifest.get("files")
    if (manifest.get("schema_version") != 1 or manifest.get("status") != "GREEN"
            or not isinstance(rows, list) or not rows
            or manifest.get("file_count") != len(rows)
            or not SHA256.fullmatch(str(manifest.get("bundle_sha256") or ""))):
        raise ProbeBlocked("INSTALLED_HELPER_MANIFEST_UNVERIFIED")
    matches = [entry for entry in rows if isinstance(entry, dict)
               and entry.get("path") == "scripts/rc6_operator_paper_rearm.py"]
    if len(matches) != 1:
        raise ProbeBlocked("INSTALLED_HELPER_MANIFEST_ENTRY_INVALID")
    row = matches[0]
    if (type(row.get("bytes")) is not int or not 0 < row["bytes"] <= 1048576
            or not SHA256.fullmatch(str(row.get("sha256") or ""))):
        raise ProbeBlocked("INSTALLED_HELPER_MANIFEST_ENTRY_INVALID")
    fd = os.open(HELPER, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        st = os.fstat(fd)
        if (not stat.S_ISREG(st.st_mode) or st.st_nlink != 1
                or st.st_mode & 0o022 or st.st_size != row["bytes"]):
            raise ProbeBlocked("INSTALLED_HELPER_FILE_UNTRUSTED")
        raw = os.read(fd, row["bytes"] + 1)
    finally:
        os.close(fd)
    if len(raw) != row["bytes"] or hashlib.sha256(raw).hexdigest() != row["sha256"]:
        raise ProbeBlocked("INSTALLED_HELPER_HASH_MISMATCH")
    spec = importlib.util.spec_from_file_location("rc6_a536_installed_operator_ro", HELPER)
    if spec is None or spec.loader is None:
        raise ProbeBlocked("INSTALLED_HELPER_IMPORT_UNVERIFIED")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.evaluate_rearm


def verify_existing_gate(receipt, *, now, expected_sha):
    try:
        issued = datetime.fromisoformat(receipt["issued_at_ar"])
        if issued.tzinfo is None or issued.utcoffset() != now.utcoffset():
            raise ValueError("offset")
        issued = issued.astimezone(ART)
        minute = issued.hour * 60 + issued.minute
        age = (now - issued).total_seconds()
        valid = (receipt.get("schema") == "RC6_PAPER_PREOPEN_GATE_V2"
                 and receipt.get("status") == "GO_PAPER_SCALPING"
                 and receipt.get("phase") == "T_MINUS_10"
                 and receipt.get("candidate_sha") == expected_sha
                 and receipt.get("trade_day_ar") == now.date().isoformat()
                 and issued.date() == now.date()
                 and 620 <= minute < 630 and 0 <= age <= 7 * 3600)
    except (KeyError, TypeError, ValueError, AttributeError):
        valid = False
    if not valid:
        raise ProbeBlocked("EXISTING_T10_GO_RECEIPT_NOT_VERIFIED")


def result_template(request=None, now=None):
    request = request if type(request) is dict else {}
    local = _art(now) if now is not None else datetime.now(ART)
    return {"schema": SCHEMA, "status": "NO_VERIFICADO", "reason": "CHECK_NOT_EXECUTED",
            "trade_day_ar": local.date().isoformat(),
            "observed_at_ar": local.isoformat(timespec="seconds"),
            "installed_sha": "", "installed_tree_sha": "", "image_id": "",
            "source_commit": request.get("source_commit", ""),
            "source_sha256": request.get("source_sha256", ""),
            "read_only": True, "entry_authority": False, "host_mutations": 0,
            "database_opened": False, "provider_calls": 0,
            "ppi_watch_touched": False, "real_orders_sent_by_action": 0}


def sanitize_runner_receipt(raw, *, return_code, product_sha, source_commit,
                            source_sha256):
    """Runner validates bytes from the host before exposing any artifact/log.

    A failed host observation can be preserved as NO_VERIFICADO but can never
    turn into OBSERVED_ONLY with a nonzero return code or wrong source/release.
    """
    if type(raw) is not str or not 0 < len(raw) <= 3500:
        raise ProbeBlocked("RUNNER_UNBOUNDED_RESULT")
    data = _json(raw)
    if (set(data) != RESULT_KEYS or data.get("schema") != SCHEMA
            or data.get("status") not in {"OBSERVED_ONLY", "NO_VERIFICADO"}
            or data.get("reason") not in ALLOWED_REASONS):
        raise ProbeBlocked("RUNNER_RESULT_SHAPE_INVALID")
    safety = {"read_only": True, "entry_authority": False, "host_mutations": 0,
              "database_opened": False, "provider_calls": 0,
              "ppi_watch_touched": False, "real_orders_sent_by_action": 0}
    if any(type(data.get(key)) is not type(value) or data[key] != value
           for key, value in safety.items()):
        raise ProbeBlocked("RUNNER_SAFETY_INVARIANT_INVALID")
    for key, expression in (("installed_sha", SHA), ("installed_tree_sha", SHA),
                            ("image_id", IMAGE), ("source_commit", SHA),
                            ("source_sha256", SHA256)):
        value = data.get(key)
        if type(value) is not str or (value and not expression.fullmatch(value)):
            raise ProbeBlocked("RUNNER_IDENTITY_FIELD_UNTRUSTED")
    if data["status"] == "OBSERVED_ONLY":
        if (data["reason"] != "EXISTING_GO_RECEIPT_MATCHED"
                or data.get("installed_sha") != product_sha
                or data.get("source_commit") != source_commit
                or data.get("source_sha256") != source_sha256
                or not SHA.fullmatch(str(data.get("installed_tree_sha") or ""))
                or not IMAGE.fullmatch(str(data.get("image_id") or ""))
                or return_code != 0):
            raise ProbeBlocked("RUNNER_FALSE_GREEN")
    elif return_code != 2:
        raise ProbeBlocked("RUNNER_BAD_HOLD_RETURN_CODE")
    if (type(data.get("trade_day_ar")) is not str
            or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", data["trade_day_ar"])
            or type(data.get("observed_at_ar")) is not str
            or len(data["observed_at_ar"]) > 40):
        raise ProbeBlocked("RUNNER_TIMESTAMP_SHAPE_INVALID")
    try:
        observed = datetime.fromisoformat(data["observed_at_ar"])
        if (observed.tzinfo is None
                or observed.utcoffset().total_seconds() != -10800
                or observed.date().isoformat() != data["trade_day_ar"]):
            raise ValueError("time")
    except (TypeError, ValueError):
        raise ProbeBlocked("RUNNER_TIMESTAMP_SHAPE_INVALID") from None
    return data


def probe(request, now):
    local = validate_request(request, now)
    evidence = result_template(request, local)
    tree, image = verify_release(request["expected_product_sha"])
    evaluate_rearm = verify_installed_helper()
    authorized, _reason, proof = evaluate_rearm(
        now=local, context="host", candidate_sha=request["expected_product_sha"],
        candidate_tree_sha=tree)
    if (authorized is not True or not isinstance(proof, dict)
            or proof.get("candidate_sha") != request["expected_product_sha"]
            or proof.get("candidate_tree_sha") != tree
            or proof.get("image_id") != image):
        raise ProbeBlocked("OPERATOR_INTENT_NOT_VERIFIED")
    receipt = _read_json(GATE_PATH, 4096, root_owned=True, root_directory=True)
    verify_existing_gate(receipt, now=local, expected_sha=request["expected_product_sha"])
    evidence.update(status="OBSERVED_ONLY", reason="EXISTING_GO_RECEIPT_MATCHED",
                    installed_sha=request["expected_product_sha"],
                    installed_tree_sha=tree, image_id=image)
    return evidence


def verify_droplet_identity(opener=None):
    opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}))
    found = []
    for key in ("id", "region"):
        try:
            with opener.open(urllib.request.Request(METADATA + key, method="GET"),
                             timeout=3) as reply:
                if reply.status != 200:
                    raise ProbeBlocked("DROPLET_METADATA_UNVERIFIED")
                raw = reply.read(65)
        except OSError:
            raise ProbeBlocked("DROPLET_METADATA_UNAVAILABLE") from None
        if not 0 < len(raw) <= 64:
            raise ProbeBlocked("DROPLET_METADATA_UNVERIFIED")
        found.append(raw.decode("ascii").strip())
    if tuple(found) != EXPECTED_DROPLET:
        raise ProbeBlocked("DROPLET_IDENTITY_MISMATCH")
    return True


def main():
    if sys.argv[1:] == ["--sanitize-runner"]:
        try:
            sanitized = sanitize_runner_receipt(
                os.environ["A536_HOST_RECEIPT"],
                return_code=int(os.environ["A536_HOST_RC"]),
                product_sha=os.environ["EXPECTED_PRODUCT_SHA"],
                source_commit=os.environ["GITHUB_SHA"],
                source_sha256=os.environ["EXPECTED_SOURCE_SHA256"])
            print(json.dumps(sanitized, sort_keys=True, separators=(",", ":")))
            return 0
        except (OSError, KeyError, ValueError, TypeError, json.JSONDecodeError):
            print("A536_RUNNER_SANITIZER=NO_VERIFICADO")
            return 2
    if sys.argv[1:] == ["--check-window"]:
        try:
            _window(datetime.now(ART))
            print("A536_CURRENT_ART_T10_CHECK_WINDOW=OPEN")
            return 0
        except (ValueError, TypeError, OSError):
            print("A536_CURRENT_ART_T10_CHECK_WINDOW=CLOSED")
            return 2
    if sys.argv[1:] == ["--verify-droplet"]:
        try:
            verify_droplet_identity()
            print("A536_PINNED_DROPLET=GREEN")
            return 0
        except (OSError, TypeError, ValueError, UnicodeError):
            print("A536_PINNED_DROPLET=NO_VERIFICADO")
            return 2
    if sys.argv[1:] == ["--encode-request"]:
        try:
            request = request_from_environment(now=datetime.now(ART))
            validate_request(request, datetime.now(ART))
            print(base64.b64encode(json.dumps(request, sort_keys=True,
                  separators=(",", ":")).encode()).decode())
            return 0
        except (KeyError, TypeError, ValueError, OSError):
            print("A536_REQUEST=NO_VERIFICADO")
            return 2
    evidence = result_template()
    try:
        data = base64.b64decode(os.environ["RC6_A536_REQUEST_B64"], validate=True)
        if not 0 < len(data) <= 1200:
            raise ProbeBlocked("REQUEST_SIZE_INVALID")
        request = _json(data)
        evidence = probe(request, datetime.now(ART))
    except (OSError, KeyError, ValueError, TypeError, AttributeError,
            json.JSONDecodeError, subprocess.SubprocessError, UnicodeError) as exc:
        evidence["reason"] = (str(exc) if isinstance(exc, ProbeBlocked)
                              else "HOST_READONLY_PROBE_UNVERIFIED")
    # No raw host file content, operator provenance IDs, credentials or quotes.
    print(json.dumps(evidence, sort_keys=True, separators=(",", ":")))
    return 0 if evidence["status"] == "OBSERVED_ONLY" else 2


if __name__ == "__main__":
    raise SystemExit(main())
