#!/usr/bin/env python3
"""A420: bounded source-cycle layout receipt, not a source/DB reader.

Only fixed file roles, coarse ages, safe counts and fixed systemd state labels
can leave the droplet. No command-line path input, provider access, SQLite,
Docker, PPI Watch, secret files, process environment or order interfaces.

This distinguishes a missing optional primary JSON cache from a missing or
stale source-cycle *producer*. It does not establish why a DB fallback failed.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import resource
import stat
import subprocess
import sys
from zoneinfo import ZoneInfo


SCHEMA = "rc6.a420.primary-source-layout-readonly.v2"
STATE_SCHEMA = "rc6.source-cycle-dispatch.v1"
MARKET_ROOT = Path("/opt/porota-trading/data/market")
FILES = (
    ("PRIMARY_MARKET_CACHE", MARKET_ROOT / "primary_last.json", 12 * 1024 * 1024, False),
    ("PRIMARY_APP_CACHE", Path("/app/data/market/primary_last.json"), 12 * 1024 * 1024, False),
    ("SOURCE_CYCLE_STATE", MARKET_ROOT / "rc6_source_cycle_dispatch_state.json", 4096, True),
    ("FULL_UNIVERSE_EXPORT", MARKET_ROOT / "rc6_instrument_evidence_latest.json", 48 * 1024 * 1024, False),
    ("SOURCE_CYCLE_SCRIPT", Path("/opt/porota-trading/scripts/rc6_source_cycle_dispatch.py"), 2 * 1024 * 1024, False),
    ("FULL_UNIVERSE_SCRIPT", Path("/opt/porota-trading/scripts/rc6_full_universe_evidence.py"), 2 * 1024 * 1024, False),
    # The scanner is not deployed yet; MISSING means NO_VERIFICADO, never GREEN.
    ("PPI_SCANNER_PRIVATE_RECEIPT", MARKET_ROOT / "rc6_ppi_scanner_primary_latest.json", 2 * 1024 * 1024, False),
)
UNIT_NAMES = ("porota-iol-shadow-collector-rc6.timer",
              "porota-iol-shadow-collector-rc6.service")
ART = ZoneInfo("America/Argentina/Buenos_Aires")
SAFE_LOAD = {"loaded", "not-found", "masked", "error"}
SAFE_ACTIVE = {"active", "inactive", "failed", "activating", "deactivating", "reloading"}
SAFE_RESULT = {"success", "exit-code", "timeout", "signal", "core-dump",
               "watchdog", "start-limit-hit", "protocol", "resources", "dependency"}

# A551 reads *only* an already-produced host export. The public result is a
# bounded aggregation, never individual tickers, prices, account data or GO.
FAMILY_SCHEMA = "rc6.a551.existing-family-aggregate-readonly.v1"
FAMILY_EXPORT_SCHEMA = "rc6-full-universe-instrument-evidence-v1"
FAMILY_EXPORT = MARKET_ROOT / "rc6_instrument_evidence_latest.json"
FAMILY_EXPORT_MAX = 48 * 1024 * 1024
FAMILY_MEMORY_MAX = 256 * 1024 * 1024
FAMILY_NAMES = tuple(sorted({
    "ACCIONES", "CEDEARS", "ETFS", "BONOS", "LETRAS", "OBLIGACIONES",
    "OPCIONES", "FUTUROS", "CAUCIONES", "FCI",
}))
FAMILY_METRICS = (
    "instrument_count", "paper_shadow_ready", "paper_contract_ready",
    "catalog_contract_candidates", "paper_auto_enabled", "pending",
    "pending_specialized_receipt", "blocked", "ingestion_attempted",
    "ppi_fresh", "iol_fresh", "byma_fresh",
)
FAMILY_COUNT_KEYS = (
    "paper_shadow_ready", "paper_contract_ready", "catalog_contract_candidates",
    "paper_auto_enabled", "ready_paper_shadow", "ready_shadow_partial",
    "ready_paper_partial_source", "pending_evidence", "pending_specialized_receipt",
    "blocked", "ingestion_attempted", "ppi_fresh", "iol_fresh", "byma_fresh",
)
# Both are called v1 upstream. Distinguish by exact fields: deployed PRODUCT
# lacks the candidate's specialist valuation and catalog-candidate counters.
FAMILY_METRICS_LEGACY = tuple(name for name in FAMILY_METRICS
                              if name not in {"catalog_contract_candidates",
                                              "pending_specialized_receipt"})
FAMILY_COUNT_KEYS_LEGACY = tuple(name for name in FAMILY_COUNT_KEYS
                                if name not in {"catalog_contract_candidates",
                                                "pending_specialized_receipt"})
FAMILY_VARIANTS = {"PRODUCT_BASELINE", "CANDIDATE_EXTENDED"}
FAMILY_REASONS = frozenset({
    "NOT_EXECUTED", "AGGREGATE_MATCHED", "MISSING", "UNSAFE_PATH", "UNSAFE_FILE", "SIZE_LIMIT",
    "UNAVAILABLE_OR_ALIAS", "UNTRUSTED_OWNER", "CHANGED_DURING_READ",
    "BAD_SCHEMA", "BAD_SHAPE", "BAD_COUNTER", "BAD_LINEAGE", "STALE_EXPORT",
    "UNSAFE_TIMESTAMP", "RESOURCE_LIMIT_UNAVAILABLE", "HOST_PROBE_UNAVAILABLE",
})
FAMILY_HASH = re.compile(r"[0-9a-f]{64}\Z")


class Rejected(Exception):
    """A fixed, sanitized reason, never the underlying path/message."""


def _open_bounded(path: Path, maximum: int) -> tuple[int, os.stat_result]:
    """Anchor *each* path component without following aliases or devices."""
    if not path.is_absolute() or ".." in path.parts or maximum <= 0:
        raise Rejected("UNSAFE_PATH")
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
    directory = None
    fd = None
    try:
        directory = os.open("/", flags | os.O_DIRECTORY)
        for part in path.parts[1:-1]:
            next_directory = os.open(part, flags | os.O_DIRECTORY, dir_fd=directory)
            os.close(directory)
            directory = next_directory
        fd = os.open(path.name, flags | os.O_NONBLOCK, dir_fd=directory)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise Rejected("UNSAFE_FILE")
        if info.st_size > maximum:
            raise Rejected("SIZE_LIMIT")
        return fd, info
    except FileNotFoundError as exc:
        raise Rejected("MISSING") from exc
    except (NotADirectoryError, IsADirectoryError) as exc:
        raise Rejected("UNSAFE_PATH") from exc
    except OSError as exc:
        # This includes symlink ELOOP, blocked permission and inaccessible mount.
        raise Rejected("UNAVAILABLE_OR_ALIAS") from exc
    finally:
        if directory is not None:
            os.close(directory)
        if fd is not None and sys.exc_info()[0] is not None:
            os.close(fd)


def _age_bucket(value: float | None, now: datetime) -> str:
    if value is None:
        return "UNKNOWN"
    age = now.timestamp() - value
    if age < -30:
        return "FUTURE_CLOCK"
    if age < 300:
        return "WITHIN_5M"
    if age < 1800:
        return "WITHIN_30M"
    if age < 7200:
        return "WITHIN_2H"
    if age < 86400:
        return "WITHIN_DAY"
    return "OLDER_THAN_DAY"


def _state_receipt(raw: bytes, now: datetime) -> dict[str, str]:
    result = {"state_schema": "NO_VERIFICADO", "session": "NO_VERIFICADO",
              "public_last_success": "UNKNOWN", "evidence_last_success": "UNKNOWN"}
    try:
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict) or data.get("schema") != STATE_SCHEMA:
            return result
        result["state_schema"] = "MATCHED"
        local_day = now.astimezone(ART).date().isoformat()
        session = data.get("session_date")
        result["session"] = "TODAY" if session == local_day else "OTHER_DAY_OR_MISSING"
        for key, output in (("last_public_success_at", "public_last_success"),
                            ("last_evidence_success_at", "evidence_last_success")):
            value = data.get(key)
            if not isinstance(value, str):
                continue
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                result[output] = _age_bucket(parsed.timestamp(), now)
    except (ValueError, TypeError, UnicodeError, OverflowError):
        pass
    return result


def inspect_file(role: str, path: Path, maximum: int, is_state: bool,
                 *, now: datetime | None = None) -> dict[str, object]:
    now = now or datetime.now(timezone.utc)
    receipt: dict[str, object] = {"role": role, "status": "NO_VERIFICADO",
                                   "mtime_age_bucket": "UNKNOWN"}
    fd = None
    try:
        fd, info = _open_bounded(path, maximum)
        receipt["status"] = "PRESENT_REGULAR"
        receipt["mtime_age_bucket"] = _age_bucket(info.st_mtime, now)
        if is_state:
            raw = os.read(fd, maximum + 1)
            final = os.fstat(fd)
            if (len(raw) != info.st_size or
                    (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns) !=
                    (final.st_dev, final.st_ino, final.st_size, final.st_mtime_ns)):
                raise Rejected("CHANGED_DURING_READ")
            receipt.update(_state_receipt(raw, now))
    except Rejected as exc:
        receipt.update(status=str(exc), mtime_age_bucket="UNKNOWN")
    finally:
        if fd is not None:
            os.close(fd)
    return receipt


def _verify_sidecar_access(path: Path, maximum: int, effective_user: str,
                           expected: os.stat_result) -> str:
    """DAC-only read probe: no bytes read, no shell, no persistent UID change.

    This cannot reproduce arbitrary systemd mount namespace restrictions.
    A real service-context proof remains NO_VERIFICADO until runtime receipt.
    """
    if os.geteuid() != 0 or effective_user not in {"ROOT", "UID1000"}:
        return "NO_VERIFICADO"
    if effective_user == "ROOT":
        # The existing host probe runs with sudo -n; _open_bounded already
        # succeeded as root and did not consume the private file's content.
        return "ACCESS_OK"
    if not hasattr(os, "fork"):
        return "NO_VERIFICADO"
    try:
        pid = os.fork()
    except OSError:
        return "NO_VERIFICADO"
    if pid == 0:
        # Child only. Never change the controlling host-probe process identity.
        try:
            try:
                gid = pwd.getpwuid(1000).pw_gid
            except KeyError:
                gid = 1000
            os.setgroups([])
            os.setgid(gid)
            os.setuid(1000)
            fd, actual = _open_bounded(path, maximum)
            os.close(fd)
            # Fail closed on a swap between the root metadata read and the
            # unprivileged DAC probe. A different regular file proves nothing.
            if ((actual.st_dev, actual.st_ino, actual.st_size, actual.st_mtime_ns,
                 actual.st_ctime_ns,
                 actual.st_uid, stat.S_IMODE(actual.st_mode)) !=
                (expected.st_dev, expected.st_ino, expected.st_size, expected.st_mtime_ns,
                 expected.st_ctime_ns,
                 expected.st_uid, stat.S_IMODE(expected.st_mode))):
                os._exit(2)
            os._exit(0)
        except Rejected:
            os._exit(1)
        except (OSError, ValueError):
            os._exit(2)
    try:
        _, status = os.waitpid(pid, 0)
    except OSError:
        return "NO_VERIFICADO"
    if not os.WIFEXITED(status):
        return "NO_VERIFICADO"
    # A non-root open failure could be DAC, a disappearing file or a changed
    # mount; do not mislabel all of them as a definitive permission denial.
    return {0: "ACCESS_OK", 1: "READ_FAILED"}.get(os.WEXITSTATUS(status), "NO_VERIFICADO")


def inspect_private_receipt(path: Path, maximum: int, *, effective_user: str,
                            now: datetime | None = None,
                            expected_owner_uid: int = 1000) -> dict[str, object]:
    """Fixed-role, privacy-preserving metadata; never read quote bytes."""
    now = now or datetime.now(timezone.utc)
    result: dict[str, object] = {
        "role": "PPI_SCANNER_PRIVATE_RECEIPT", "status": "NO_VERIFICADO",
        "mtime_age_bucket": "UNKNOWN", "private_mode": "UNKNOWN",
        "owner_class": "UNKNOWN", "consumer_access": "NO_VERIFICADO",
        "access_scope": "HOST_DAC_ONLY",
    }
    fd = None
    try:
        fd, info = _open_bounded(path, maximum)
        # SECURITY A473: _verify_sidecar_access forks and drops to UID1000.
        # An inherited root-open descriptor would bypass that DAC check even
        # after setuid. Close the privileged descriptor before ANY fork.
        os.close(fd)
        fd = None
        result["mtime_age_bucket"] = _age_bucket(info.st_mtime, now)
        mode_ok = stat.S_IMODE(info.st_mode) == 0o600
        owner_ok = info.st_uid == expected_owner_uid
        result["private_mode"] = "PRIVATE_0600" if mode_ok else "OTHER"
        result["owner_class"] = "UID1000" if owner_ok else "OTHER"
        if not mode_ok:
            result["status"] = "UNSAFE_MODE"
        elif not owner_ok:
            result["status"] = "OWNER_MISMATCH"
        else:
            result["status"] = "PRESENT_REGULAR"
            result["consumer_access"] = _verify_sidecar_access(path, maximum, effective_user, info)
    except Rejected as exc:
        result["status"] = str(exc)
    finally:
        if fd is not None:
            os.close(fd)
    return result


def _safe_label(value: str | None, whitelist: set[str]) -> str:
    return value if value in whitelist else "UNKNOWN"


def _effective_user_class(user: str | None, dynamic_user: str | None) -> str:
    if dynamic_user == "yes":
        return "DYNAMIC"
    if dynamic_user != "no" or user is None:
        return "UNKNOWN"
    if user in ("", "root", "0"):
        return "ROOT"
    if user == "1000":
        return "UID1000"
    try:
        uid = int(user) if user.isascii() and user.isdecimal() else pwd.getpwnam(user).pw_uid
        return "ROOT" if uid == 0 else "UID1000" if uid == 1000 else "OTHER"
    except (KeyError, ValueError, OverflowError, OSError):
        return "UNKNOWN"


def inspect_unit(unit: str, *, runner=subprocess.run) -> dict[str, str]:
    """Use systemctl show ONLY. Never start, restart, enable, or inspect env."""
    result = {"unit": "CYCLE_TIMER" if unit.endswith(".timer") else "CYCLE_SERVICE",
              "query": "UNAVAILABLE", "load": "UNKNOWN", "active": "UNKNOWN",
              "result": "UNKNOWN", "main_exit": "UNKNOWN",
              "effective_user": "UNKNOWN", "dynamic_user": "UNKNOWN", "dropins": "UNKNOWN"}
    if unit not in UNIT_NAMES:
        return result
    try:
        completed = runner(["systemctl", "show", "--no-pager", "--property=LoadState",
                            "--property=ActiveState", "--property=Result",
                            "--property=ExecMainStatus", "--property=User",
                            "--property=DynamicUser", "--property=DropInPaths", unit],
                           capture_output=True, text=True, timeout=4, check=False)
        if completed.returncode != 0 or len(completed.stdout) > 4096:
            return result
        pairs = dict(line.split("=", 1) for line in completed.stdout.splitlines()
                     if "=" in line and len(line) <= 2048)
        result.update(query="READ_ONLY", load=_safe_label(pairs.get("LoadState"), SAFE_LOAD),
                      active=_safe_label(pairs.get("ActiveState"), SAFE_ACTIVE),
                      result=_safe_label(pairs.get("Result"), SAFE_RESULT))
        status = pairs.get("ExecMainStatus", "")
        if status.isascii() and status.isdecimal() and len(status) <= 4:
            result["main_exit"] = "ZERO" if int(status) == 0 else "NONZERO"
        if unit.endswith(".timer"):
            result.update(effective_user="NOT_APPLICABLE", dynamic_user="NOT_APPLICABLE",
                          dropins="NOT_APPLICABLE")
        else:
            dynamic = pairs.get("DynamicUser")
            result["dynamic_user"] = {"yes": "YES", "no": "NO"}.get(dynamic, "UNKNOWN")
            result["effective_user"] = (_effective_user_class(pairs.get("User"), dynamic)
                                        if result["load"] == "loaded" else "UNKNOWN")
            dropins = pairs.get("DropInPaths")
            result["dropins"] = ("PRESENT" if dropins else "NONE") if dropins is not None else "UNKNOWN"
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    return result


def inspect_all(*, now: datetime | None = None, files=FILES,
                runner=subprocess.run) -> dict[str, object]:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("aware time required")
    units = [inspect_unit(unit, runner=runner) for unit in UNIT_NAMES]
    consumer = units[-1]["effective_user"] if len(units) == len(UNIT_NAMES) else "UNKNOWN"
    inspected = [inspect_private_receipt(path, maximum, effective_user=consumer, now=now)
                 if role == "PPI_SCANNER_PRIVATE_RECEIPT"
                 else inspect_file(role, path, maximum, is_state, now=now)
                 for role, path, maximum, is_state in files]
    return {"schema": SCHEMA, "read_only": True, "host_metadata_only": True,
            "database_opened": False, "provider_calls": 0,
            "order_routes_called": False, "ppi_watch_touched": False,
            "paper_ready": False, "diagnostic_only": True,
            "files": inspected, "units": units}


def default_family_receipt(reason: str = "NOT_EXECUTED") -> dict[str, object]:
    return {"schema": FAMILY_SCHEMA, "status": "NO_VERIFICADO", "reason": reason,
            "read_only": True, "aggregates_only": True, "database_opened": False,
            "provider_calls": 0, "order_routes_called": False,
            "ppi_watch_touched": False, "real_orders_sent_by_action": 0,
            "paper_ready": False, "ppi_book_verified": False,
            "causal_fills_verified": False, "generated_at_utc": "",
            "source_sha256": "", "catalog_count": 0, "ledger_count": 0,
            "ledger_complete": False, "extra_family_count": 0, "families": [],
            "source_variant": "NO_VERIFICADO", "specialized_receipt_guard_present": False}


def _distinct_json_keys(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise Rejected("BAD_SHAPE")
        result[name] = value
    return result


def _positive_int(value: object, maximum: int) -> bool:
    return type(value) is int and 0 <= value <= maximum


def _producer_row_metrics(row: object, *, extended: bool) -> tuple[str, dict[str, int], bool]:
    """Recompute counters independently of the producer's summary fields."""
    if type(row) is not dict or type(row.get("family")) is not str:
        raise Rejected("BAD_SHAPE")
    family = row["family"]
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,39}", family):
        raise Rejected("BAD_SHAPE")
    if (type(row.get("paper_contract_ready")) is not bool or
            (extended and type(row.get("catalog_contract_candidate")) is not bool) or
            (not extended and "catalog_contract_candidate" in row) or
            row.get("paper_auto_enabled") is not False or
            type(row.get("ingestion")) is not dict or
            type(row["ingestion"].get("any_source_attempted")) is not bool or
            type(row.get("sources")) is not dict):
        raise Rejected("BAD_SHAPE")
    source = row["sources"]
    freshness = {}
    for name in ("PPI", "IOL", "BYMA"):
        item = source.get(name)
        if (type(item) is not dict or type(item.get("freshness")) is not dict or
                item["freshness"].get("state") not in {"FRESH", "STALE", "UNKNOWN"}):
            raise Rejected("BAD_SHAPE")
        freshness[name] = item["freshness"]["state"] == "FRESH"
    if type(source["PPI"].get("selection_eligible")) is not bool:
        raise Rejected("BAD_SHAPE")
    if source["PPI"]["selection_eligible"] and not freshness["PPI"]:
        raise Rejected("BAD_COUNTER")
    status = row.get("status")
    if status not in {"PENDING_EVIDENCE", "PENDING_SPECIALIZED_VALUATION_RECEIPT",
                      "BLOCKED_SOURCE_REVIEW", "READY_PAPER_SHADOW",
                      "READY_PAPER_PARTIAL_SOURCE", "READY_SHADOW_PARTIAL"}:
        raise Rejected("BAD_SHAPE")
    if not extended and status == "PENDING_SPECIALIZED_VALUATION_RECEIPT":
        raise Rejected("BAD_SHAPE")
    ready = row["paper_contract_ready"]
    candidate = row["catalog_contract_candidate"] if extended else False
    if extended and ready and not candidate:
        raise Rejected("BAD_COUNTER")
    metrics = {
        "instrument_count": 1,
        "paper_shadow_ready": int(ready),
        "paper_contract_ready": int(ready),
        "catalog_contract_candidates": int(candidate),
        "paper_auto_enabled": 0,
        "pending": int(status == "PENDING_EVIDENCE"),
        "pending_specialized_receipt": int(status == "PENDING_SPECIALIZED_VALUATION_RECEIPT"),
        "blocked": int(status.startswith("BLOCKED")),
        "ingestion_attempted": int(row["ingestion"]["any_source_attempted"]),
        "ppi_fresh": int(freshness["PPI"]),
        "iol_fresh": int(freshness["IOL"]),
        "byma_fresh": int(freshness["BYMA"]),
    }
    return family, metrics, source["PPI"]["selection_eligible"]


def _family_aggregate(data: object, *, now: datetime, digest: str) -> dict[str, object]:
    if (type(data) is not dict or data.get("schema") != FAMILY_EXPORT_SCHEMA or
            data.get("decision_effect") != "OBSERVE_ONLY" or
            data.get("real_money_authorized") is not False or
            data.get("paper_shadow_only") is not True):
        raise Rejected("BAD_SCHEMA")
    raw_time = data.get("generated_at")
    try:
        generated = datetime.fromisoformat(raw_time)
        if generated.tzinfo is None or now.tzinfo is None:
            raise ValueError("naive")
        age = (now - generated.astimezone(timezone.utc)).total_seconds()
        if age < -30:
            raise Rejected("UNSAFE_TIMESTAMP")
        if age > 300:
            raise Rejected("STALE_EXPORT")
        if generated.astimezone(ART).date() != now.astimezone(ART).date():
            raise Rejected("STALE_EXPORT")
    except (ValueError, TypeError, OverflowError, AttributeError):
        raise Rejected("UNSAFE_TIMESTAMP") from None
    universe, rows, summaries, totals = (data.get(key) for key in
                                         ("universe", "instruments", "families", "counts"))
    if (type(universe) is not dict or type(rows) is not list or
            type(summaries) is not list or type(totals) is not dict or
            len(rows) > 100_000 or len(summaries) > 100):
        raise Rejected("BAD_SHAPE")
    if set(totals) == set(FAMILY_COUNT_KEYS):
        extended = True
    elif set(totals) == set(FAMILY_COUNT_KEYS_LEGACY):
        extended = False
    else:
        raise Rejected("BAD_SHAPE")
    metrics_version = FAMILY_METRICS if extended else FAMILY_METRICS_LEGACY
    counts_version = FAMILY_COUNT_KEYS if extended else FAMILY_COUNT_KEYS_LEGACY
    catalog, ledger = universe.get("catalog_count"), universe.get("ledger_count")
    if (not _positive_int(catalog, 100_000) or not _positive_int(ledger, 100_000) or
            type(universe.get("ledger_complete")) is not bool or
            ledger != len(rows) or (universe["ledger_complete"] and catalog != ledger)):
        raise Rejected("BAD_COUNTER")
    actual: dict[str, dict[str, int]] = {}
    eligible: dict[str, int] = {}
    global_statuses: dict[str, int] = {name: 0 for name in FAMILY_COUNT_KEYS}
    for row in rows:
        family, values, ppi_eligible = _producer_row_metrics(row, extended=extended)
        bucket = actual.setdefault(family, {name: 0 for name in FAMILY_METRICS})
        for key, value in values.items():
            bucket[key] += value
        eligible[family] = eligible.get(family, 0) + int(ppi_eligible)
        status = row["status"]
        for key, value in (("paper_shadow_ready", values["paper_contract_ready"]),
                           ("paper_contract_ready", values["paper_contract_ready"]),
                           ("catalog_contract_candidates", values["catalog_contract_candidates"]),
                           ("ingestion_attempted", values["ingestion_attempted"]),
                           ("ppi_fresh", values["ppi_fresh"]),
                           ("iol_fresh", values["iol_fresh"]),
                           ("byma_fresh", values["byma_fresh"]),
                           ("ready_paper_shadow", int(status == "READY_PAPER_SHADOW")),
                           ("ready_shadow_partial", int(status == "READY_SHADOW_PARTIAL")),
                           ("ready_paper_partial_source", int(status == "READY_PAPER_PARTIAL_SOURCE")),
                           ("pending_evidence", values["pending"]),
                           ("pending_specialized_receipt", values["pending_specialized_receipt"]),
                           ("blocked", values["blocked"])):
            global_statuses[key] += value
    raw_family: dict[str, dict[str, int]] = {}
    for item in summaries:
        if (type(item) is not dict or set(item) != set(metrics_version) | {"family"} or
                type(item.get("family")) is not str or
                item["family"] in raw_family):
            raise Rejected("BAD_SHAPE")
        name = item["family"]
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,39}", name):
            raise Rejected("BAD_SHAPE")
        values = {k: item.get(k, 0) for k in FAMILY_METRICS}
        if any(not _positive_int(v, ledger) for v in values.values()):
            raise Rejected("BAD_COUNTER")
        raw_family[name] = values
    if extended:
        for name in FAMILY_NAMES:
            actual.setdefault(name, {key: 0 for key in FAMILY_METRICS})
    if set(raw_family) != set(actual) or any(raw_family[k] != actual[k] for k in actual):
        raise Rejected("BAD_COUNTER")
    if any(not _positive_int(totals.get(k), ledger) or totals[k] != global_statuses[k]
           for k in counts_version):
        raise Rejected("BAD_COUNTER")
    for name in FAMILY_NAMES:
        actual.setdefault(name, {key: 0 for key in FAMILY_METRICS})
    exported = []
    for name in FAMILY_NAMES:
        values = actual[name]
        exported.append({"family": name, "instrument_count": values["instrument_count"],
                         "producer_contract_ready": values["paper_contract_ready"],
                         "catalog_contract_candidates": values["catalog_contract_candidates"]
                         if extended else None,
                         "ingestion_attempted": values["ingestion_attempted"],
                         "ppi_source_fresh": values["ppi_fresh"],
                         "ppi_selection_eligible": eligible.get(name, 0),
                         "pending_evidence": values["pending"],
                         "pending_specialized_receipt": values["pending_specialized_receipt"],
                         "blocked_source_review": values["blocked"]})
    result = default_family_receipt()
    result.update(status="OBSERVED_AGGREGATE", reason="AGGREGATE_MATCHED",
                  generated_at_utc=generated.astimezone(timezone.utc).isoformat(),
                  source_sha256=digest, catalog_count=catalog, ledger_count=ledger,
                  ledger_complete=universe["ledger_complete"],
                  extra_family_count=sum(values["instrument_count"] for name, values in actual.items()
                                         if name not in FAMILY_NAMES), families=exported,
                  source_variant="CANDIDATE_EXTENDED" if extended else "PRODUCT_BASELINE",
                  specialized_receipt_guard_present=extended)
    return result


def inspect_family_rollup(*, now: datetime | None = None, path: Path = FAMILY_EXPORT) -> dict[str, object]:
    """One bounded host-only read; a valid aggregate is NEVER entry authority."""
    now = now or datetime.now(timezone.utc)
    fd = None
    try:
        fd, before = _open_bounded(path, FAMILY_EXPORT_MAX)
        if before.st_uid != 0 or before.st_mode & 0o022:
            raise Rejected("UNTRUSTED_OWNER")
        if not 32 <= before.st_size <= FAMILY_EXPORT_MAX:
            raise Rejected("SIZE_LIMIT")
        age = now.timestamp() - before.st_mtime
        if age < -30:
            raise Rejected("UNSAFE_TIMESTAMP")
        if age > 300:
            raise Rejected("STALE_EXPORT")
        raw = os.read(fd, before.st_size + 1)
        after = os.fstat(fd)
        keys = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_uid", "st_mode")
        if len(raw) != before.st_size or any(getattr(before, k) != getattr(after, k) for k in keys):
            raise Rejected("CHANGED_DURING_READ")
        def no_constant(_):
            raise Rejected("BAD_SHAPE")
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_distinct_json_keys,
                          parse_constant=no_constant)
        return _family_aggregate(data, now=now, digest=hashlib.sha256(raw).hexdigest())
    except Rejected as exc:
        return default_family_receipt(str(exc))
    except (OSError, ValueError, TypeError, UnicodeError, MemoryError, OverflowError):
        return default_family_receipt("BAD_SHAPE")
    finally:
        if fd is not None:
            os.close(fd)


def sanitize_family_rollup(raw: bytes, *, host_returncode: int) -> dict[str, object]:
    """Runner refuses any extra key, raw quote, symbol, account or forged GO."""
    fallback = default_family_receipt("HOST_PROBE_UNAVAILABLE")
    if host_returncode != 0 or not isinstance(raw, bytes) or not 0 < len(raw) <= 12_000:
        return fallback
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_distinct_json_keys)
        if (type(data) is not dict or set(data) != set(fallback) or
                data["schema"] != FAMILY_SCHEMA or
                data["status"] not in {"OBSERVED_AGGREGATE", "NO_VERIFICADO"} or
                data["reason"] not in FAMILY_REASONS):
            return fallback
        for key in ("read_only", "aggregates_only", "database_opened", "provider_calls",
                    "order_routes_called", "ppi_watch_touched", "real_orders_sent_by_action",
                    "paper_ready", "ppi_book_verified", "causal_fills_verified"):
            if type(data[key]) is not type(fallback[key]) or data[key] != fallback[key]:
                return fallback
        if data["status"] == "NO_VERIFICADO":
            return data if data == default_family_receipt(data["reason"]) else fallback
        if (data["reason"] != "AGGREGATE_MATCHED" or
                data["source_variant"] not in FAMILY_VARIANTS or
                type(data["specialized_receipt_guard_present"]) is not bool or
                data["specialized_receipt_guard_present"] != (
                    data["source_variant"] == "CANDIDATE_EXTENDED") or
                not isinstance(data["source_sha256"], str) or
                not FAMILY_HASH.fullmatch(data["source_sha256"]) or
                not isinstance(data["generated_at_utc"], str) or
                len(data["generated_at_utc"]) > 48 or
                type(data["catalog_count"]) is not int or
                type(data["ledger_count"]) is not int or
                type(data["extra_family_count"]) is not int or
                type(data["ledger_complete"]) is not bool or
                not 0 <= data["ledger_count"] <= 100_000 or
                not 0 <= data["catalog_count"] <= 100_000 or
                not 0 <= data["extra_family_count"] <= data["ledger_count"]):
            return fallback
        moment = datetime.fromisoformat(data["generated_at_utc"])
        if moment.tzinfo is None or moment.utcoffset() != timezone.utc.utcoffset(moment):
            return fallback
        rows = data["families"]
        fields = {"family", "instrument_count", "producer_contract_ready",
                  "catalog_contract_candidates", "ingestion_attempted", "ppi_source_fresh",
                  "ppi_selection_eligible", "pending_evidence", "pending_specialized_receipt",
                  "blocked_source_review"}
        if type(rows) is not list or len(rows) != len(FAMILY_NAMES):
            return fallback
        total = 0
        for expected, row in zip(FAMILY_NAMES, rows):
            if (type(row) is not dict or set(row) != fields or row["family"] != expected or
                    any(not _positive_int(row[field], data["ledger_count"])
                        for field in fields - {"family", "catalog_contract_candidates"}) or
                    (data["source_variant"] == "CANDIDATE_EXTENDED" and
                     not _positive_int(row["catalog_contract_candidates"], data["ledger_count"])) or
                    (data["source_variant"] == "PRODUCT_BASELINE" and
                     row["catalog_contract_candidates"] is not None) or
                    row["ppi_selection_eligible"] > row["ppi_source_fresh"] or
                    (data["source_variant"] == "CANDIDATE_EXTENDED" and
                     row["producer_contract_ready"] > row["catalog_contract_candidates"]) or
                    max(row["pending_evidence"], row["pending_specialized_receipt"],
                        row["blocked_source_review"]) > row["instrument_count"]):
                return fallback
            total += row["instrument_count"]
        if total + data["extra_family_count"] != data["ledger_count"]:
            return fallback
        return data
    except (ValueError, TypeError, KeyError, UnicodeError, OverflowError):
        return fallback


def _limit_family_probe_memory():
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_AS)
        resource.setrlimit(resource.RLIMIT_AS,
                           (min(soft, FAMILY_MEMORY_MAX) if soft != resource.RLIM_INFINITY
                            else FAMILY_MEMORY_MAX, hard))
    except (ValueError, OSError):
        raise Rejected("RESOURCE_LIMIT_UNAVAILABLE") from None


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["host-probe"]:
        print(json.dumps(inspect_all(), sort_keys=True, separators=(",", ":"),
                         ensure_ascii=True))
        return 0
    if args == ["family-host-probe"]:
        try:
            _limit_family_probe_memory()
            result = inspect_family_rollup()
        except Rejected as exc:
            result = default_family_receipt(str(exc))
        print(json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=True))
        return 0
    if args == ["family-sanitize"]:
        try:
            with Path("evidence/.a551_family_raw.json").open("rb") as source:
                raw = source.read(12_001)
            code = int(os.environ.get("A551_REMOTE_RC", "2"))
            result = sanitize_family_rollup(raw, host_returncode=code)
        except (OSError, ValueError):
            result = default_family_receipt("HOST_PROBE_UNAVAILABLE")
        Path("evidence/a551_family_aggregate.json").write_text(
            json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8")
        print("A551_FAMILY_AGGREGATE=" + result["status"])
        return 0
    print("Usage: primary source layout host-probe|family-host-probe|family-sanitize",
          file=sys.stderr)
    return 64


if __name__ == "__main__":
    raise SystemExit(main())
