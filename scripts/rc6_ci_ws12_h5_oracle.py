"""Bounded external AF_UNIX protocol probe, without certification authority."""
import json
import math
import os
from pathlib import Path
import socket
import stat
import time


class OracleHold(ValueError):
    """The endpoint or its response could not be established safely."""


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise OracleHold("DUPLICATE_KEY")
        result[key] = value
    return result


def parse_response(raw):
    if (type(raw) is not bytes or len(raw) > 131073 or not raw.endswith(b"\n")
            or raw.count(b"\n") != 1):
        raise OracleHold("RESPONSE_BOUNDS")
    try:
        value = json.loads(raw, object_pairs_hook=_unique,
                           parse_constant=lambda _: (_ for _ in ()).throw(OracleHold("CONSTANT")))
    except (ValueError, UnicodeError, RecursionError):
        raise OracleHold("RESPONSE_INVALID") from None
    if type(value) is not dict:
        raise OracleHold("RESPONSE_INVALID")
    return value


def _event_json(event):
    if type(event) is not dict or not event:
        raise OracleHold("EVENT_REJECTED")
    try:
        encoded = json.dumps(event, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, RecursionError):
        raise OracleHold("EVENT_REJECTED") from None
    if len(encoded.encode("utf-8")) > 131072:
        raise OracleHold("EVENT_REJECTED")
    return encoded


def assess(pull, deny, *, expected_event):
    expected = _event_json(expected_event)
    if (type(pull) is not dict or pull.get("state") != "OK"
            or type(pull.get("events")) is not list or len(pull["events"]) != 1
            or _event_json(pull["events"][0]) != expected
            or type(deny) is not dict or deny != {"state": "DENIED_READ_ONLY"}):
        raise OracleHold("PROTOCOL_REJECTED")
    return {"protocol_probe_pass": True, "af_unix_independently_verified": False,
            "qa_certified": False}


def probe(path, *, expected_event, timeout=1.0):
    path = Path(path)
    try:
        _event_json(expected_event)
        if (type(timeout) not in (int, float) or not math.isfinite(timeout)
                or not 0 < timeout <= 5):
            raise OracleHold("TIMEOUT_REJECTED")
        deadline = time.monotonic() + timeout
        def remaining():
            value = deadline - time.monotonic()
            if value <= 0:
                raise OracleHold("DEADLINE")
            return value
        parent = path.parent.lstat()
        endpoint = path.lstat()
        if (not path.is_absolute() or path.parent.resolve(strict=True) != path.parent
                or not stat.S_ISDIR(parent.st_mode)
                or stat.S_IMODE(parent.st_mode) != 0o700
                or not stat.S_ISSOCK(endpoint.st_mode)
                or stat.S_IMODE(endpoint.st_mode) != 0o600
                or parent.st_uid != os.geteuid() or endpoint.st_uid != os.geteuid()):
            raise OracleHold("ENDPOINT_REJECTED")
        def request(packet):
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(remaining())
                client.connect(str(path))
                client.settimeout(remaining())
                client.sendall(json.dumps(packet, separators=(",", ":")).encode() + b"\n")
                data = bytearray()
                while not data.endswith(b"\n") and len(data) <= 131073:
                    client.settimeout(remaining())
                    chunk = client.recv(min(4096, 131074 - len(data)))
                    remaining()
                    if not chunk:
                        break
                    data.extend(chunk)
                return parse_response(bytes(data))
        result = assess(request({"op": "pull", "consumer": "watch", "after_sequence": 0}),
                        request({"op": "place_order", "consumer": "watch"}),
                        expected_event=expected_event)
        remaining()
        return result
    except (OSError, ValueError, TypeError):
        raise OracleHold("PROBE_REJECTED") from None
