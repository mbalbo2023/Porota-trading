"""Independent PAPER decision signer entrypoint, deliberately NOT an engine hook.

This bounded process can observe only an already COMMITTED decision freeze. It
does not read provider data, place orders, manage services, or modify PPI Watch.
Release and signer trust pins are deliberately None in the DRAFT: it therefore
cannot sign until an independently reviewed release explicitly pins both.

Do not call this from be_paper_engine's transaction or a provider callback.
The optional CLI is deliberately inert unless --sign is explicitly supplied;
its safe deployment/scheduling must be separately reviewed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import stat

import rc6_paper_causal_decision_receipt as causal


_SHA = re.compile(r"[0-9a-f]{64}\Z")

# Independent release/host trust configuration, NEVER derived from --db-path,
# a PAPER decision receipt, or the runtime environment. Deliberately unpinned
# in this DRAFT: no signing authority until a separately reviewed deployment
# binds exactly one trusted PAPER database path and key/release pins.
PINNED_DECISION_DB_ABSPATH: Path | None = None


def _denied(reason: str) -> dict[str, object]:
    return {"state": "NO_VERIFICADO", "reason": reason,
            "decision_known_at": None, "signature_created": False,
            "fill_accredited": False, "provider_gets": 0,
            "real_orders_sent": 0}


def attest_one_pinned(db_path: Path, *, decision_key: str,
                      signing_key_path: Path | None,
                      expected_release_sha256: str | None) -> dict[str, object]:
    """Sign one committed PAPER decision only under independently fixed pins.

    The signature is recorded by the existing A620 post-COMMIT observer, not
    the engine. It is never a broker/PPI certificate or fill authorization;
    verification is the responsibility of a distinct pinned reader.
    """
    release_pin = causal.PINNED_RELEASE_SHA256
    signer_pin = causal.PINNED_DECISION_SIGNER_KEY_SHA256
    if (not isinstance(release_pin, str) or not _SHA.fullmatch(release_pin)
            or not isinstance(signer_pin, str) or not _SHA.fullmatch(signer_pin)):
        return _denied("CAUSAL_SIGNER_OR_RELEASE_TRUST_UNAVAILABLE")
    pinned_db = PINNED_DECISION_DB_ABSPATH
    if not isinstance(pinned_db, Path) or not pinned_db.is_absolute():
        return _denied("CAUSAL_SIGNER_DB_TRUST_UNAVAILABLE")
    if not isinstance(db_path, Path) or not db_path.is_absolute() or db_path != pinned_db:
        return _denied("CAUSAL_SIGNER_DB_PATH_UNTRUSTED")
    try:
        # No symlink or hardlink aliases may substitute a caller-controlled DB.
        if any(part.is_symlink() for part in (db_path, *db_path.parents)):
            return _denied("CAUSAL_SIGNER_DB_ALIAS_HOLD")
        info = db_path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            return _denied("CAUSAL_SIGNER_DB_FILE_UNTRUSTED")
    except OSError:
        return _denied("CAUSAL_SIGNER_DB_FILE_UNAVAILABLE")
    if (not isinstance(expected_release_sha256, str)
            or expected_release_sha256 != release_pin):
        return _denied("CAUSAL_SIGNER_RELEASE_UNPINNED")
    if (not isinstance(decision_key, str) or not decision_key.strip()
            or signing_key_path is None):
        return _denied("CAUSAL_SIGNER_INPUT_UNVERIFIED")
    try:
        from cryptography.hazmat.primitives import serialization
        key = causal._private_ed25519_key(Path(signing_key_path))
        public = key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw)
        if hashlib.sha256(public).hexdigest() != signer_pin:
            return _denied("CAUSAL_SIGNER_KEY_UNPINNED")
    except (OSError, ValueError, ImportError, TypeError):
        # No key path, byte count, permission or credential details in output.
        return _denied("CAUSAL_SIGNER_KEY_ACCESS_HOLD")
    attested = causal.attest_after_commit(
        Path(db_path), decision_key=decision_key,
        signing_key_path=Path(signing_key_path),
        expected_release_sha256=release_pin)
    # A key swapped between the preflight open and the underlying observer's
    # second O_NOFOLLOW open must never be reported as a pinned success. The
    # immutable signed row then remains HOLD for separate incident handling.
    if (attested.get("signature_created") is True
            and attested.get("signer_key_id") != signer_pin):
        return {**attested, "state": "BLOQUEADO",
                "reason": "CAUSAL_SIGNED_RECEIPT_KEY_CHANGED_AFTER_PREFLIGHT",
                "fill_accredited": False, "provider_gets": 0}
    # Do not transform RECORDED_UNVERIFIED_UNTIL_PINNED_VERIFIER into READY.
    return {**attested, "provider_gets": 0, "fill_accredited": False}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="A667 bounded PAPER-only post-COMMIT signer")
    parser.add_argument("--db-path", type=Path, required=True)
    parser.add_argument("--decision-key", required=True)
    parser.add_argument("--signing-key-path", type=Path, required=True)
    parser.add_argument("--release-sha256", required=True)
    parser.add_argument("--sign", action="store_true", help="explicitly request pinned signing")
    args = parser.parse_args(argv)
    result = (attest_one_pinned(
        args.db_path, decision_key=args.decision_key,
        signing_key_path=args.signing_key_path,
        expected_release_sha256=args.release_sha256)
        if args.sign else _denied("CAUSAL_SIGNING_NOT_REQUESTED"))
    print(json.dumps(result, sort_keys=True, ensure_ascii=False, separators=(",", ":")))
    return 0 if result.get("signature_created") is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
