"""A667 OFFHOST-only signed decision + first future PPI Book2 diagnostic.

Only a truly post-COMMIT, independently pinned Ed25519 decision_known_at can
anchor Book2. An unhashed/self-described decision_at or the timestamp on the
snapshot is insufficient. The local Book journal has *no PPI provider signature*;
even a positive pair remains EVIDENCE_ONLY_NO_GO, never a fill or PAPER READY.

No network, provider GET, order route, PPI Watch, runtime or DB mutation.
"""
from __future__ import annotations

from contextlib import closing
from datetime import timedelta, timezone
from pathlib import Path
import re
import sqlite3
import stat
from typing import Any
from urllib.parse import quote as urlquote

import rc6_paper_causal_decision_receipt as causal
from rc6_ppi_pit_replay_bridge import NINE_FAMILY_GROUPS, causal_book_witness
from rc6_ppi_pit_two_book_guard import _book1_matches_quote
from rc6_ppi_scanner_provenance import _aware, verified_native_book_journal


SCHEMA = "rc6.ppi-signed-postcommit-book2-offhost.v1"
_SHA = re.compile(r"[a-f0-9]{64}\Z")
ART = timezone(timedelta(hours=-3))


def _base(family_group: str, decision_key: str) -> dict[str, Any]:
    return {"schema": SCHEMA, "family_group": family_group,
            "decision_key": decision_key, "state": "NO_VERIFICADO",
            "reason": "SIGNED_POSTCOMMIT_PIT_EVIDENCE_UNAVAILABLE",
            "decision_signature_verified": False,
            "decision_receipt_known_at_verified": False,
            "decision_known_at": None, "book1_quote_bound": False,
            "book1_event_sha256": None, "book2_event_sha256": None,
            "book2_after_signed_known_at": False,
            "book2_first_top_positive": False,
            "source_artifact_independently_verified": False,
            "unit_contract_verified": False, "tariff_contract_verified": False,
            "family_ready": False, "fill_accredited": False,
            "provider_gets": 0, "real_orders_sent": 0}


def _fail(result: dict[str, Any], reason: str, *, blocked: bool = False
          ) -> dict[str, Any]:
    return {**result, "state": "BLOQUEADO" if blocked else "NO_VERIFICADO",
            "reason": reason}


def _freeze_readonly(db_path: Path, decision_key: str
                     ) -> tuple[dict[str, Any] | None, str | None, str]:
    try:
        path = Path(db_path)
        if any(part.is_symlink() for part in (path, *path.parents)):
            return None, None, "PIT_SIGNED_DB_ALIAS_HOLD"
        mode = path.lstat()
        if not stat.S_ISREG(mode.st_mode) or mode.st_nlink != 1:
            return None, None, "PIT_SIGNED_DB_FILE_UNTRUSTED"
        uri = "file:" + urlquote(str(path.absolute()), safe="/") + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=2)) as connection:
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            return causal._read_verified_freeze(connection, decision_key)
    except (OSError, sqlite3.Error, ValueError):
        return None, None, "PIT_SIGNED_FREEZE_DB_UNAVAILABLE"


def inspect_pinned_signed_two_book(
    journal_path: Path, decision_db_path: Path, *, decision_key: str,
    family_group: str, identity: tuple[str, str, str, str, str],
    latency_seconds: float, trusted_public_key_raw: bytes | None,
    trusted_public_key_sha256: str | None,
    trusted_release_sha256: str | None, max_book1_age_seconds: float = 120,
) -> dict[str, Any]:
    """Verify signed known-at and bind Book2 strictly later; no PAPER authority.

    The verifier's public key/release pins must be independently deployed,
    never copied from the receipt. In a DRAFT those pins are None, therefore
    this bridge rejects even a well-formed synthetic signature by default.
    """
    result = _base(family_group, decision_key)
    if (not isinstance(decision_key, str) or not decision_key.strip()
            or family_group not in NINE_FAMILY_GROUPS
            or not isinstance(identity, tuple) or len(identity) != 5
            or any(not isinstance(part, str) or not part.strip() for part in identity)
            or isinstance(latency_seconds, bool)
            or not isinstance(latency_seconds, (int, float))
            or not 0 <= latency_seconds <= 300
            or isinstance(max_book1_age_seconds, bool)
            or not isinstance(max_book1_age_seconds, (int, float))
            or not 0 < max_book1_age_seconds <= 300):
        return _fail(result, "PIT_SIGNED_INPUT_INVALID", blocked=True)
    if family_group == "FCI":
        return _fail(result, "FCI_NAV_CUTOFF_NOT_EXCHANGE_BOOK", blocked=True)

    pinned = causal.verify_signed_receipt(
        Path(decision_db_path), decision_key=decision_key,
        trusted_public_key_raw=trusted_public_key_raw,
        trusted_public_key_sha256=trusted_public_key_sha256,
        trusted_release_sha256=trusted_release_sha256)
    if (pinned.get("state") != "SIGNED_DECISION_KNOWN_AT_VERIFIED"
            or pinned.get("decision_signature_verified") is not True
            or pinned.get("decision_known_at_verified") is not True):
        return _fail(result, str(pinned.get("reason") or "CAUSAL_PINNED_RECEIPT_ABSENT"),
                     blocked=pinned.get("state") == "BLOQUEADO")
    known_at = _aware(pinned.get("decision_known_at"))
    if known_at is None:
        return _fail(result, "PIT_SIGNED_KNOWN_AT_INVALID", blocked=True)
    result.update(decision_signature_verified=True,
                  decision_receipt_known_at_verified=True,
                  decision_known_at=pinned["decision_known_at"],
                  signed_decision_receipt_sha256=pinned.get("receipt_sha256"))

    frozen, freeze_sha, error = _freeze_readonly(Path(decision_db_path), decision_key)
    if error or frozen is None or freeze_sha != pinned.get("freeze_sha256"):
        return _fail(result, error or "PIT_SIGNED_FREEZE_CHANGED", blocked=True)
    if (frozen.get("identity") != list(identity)
            or frozen.get("release_sha256_claim") != trusted_release_sha256
            or frozen.get("action") not in {"BUY", "SELL"}):
        return _fail(result, "PIT_SIGNED_FREEZE_IDENTITY_MISMATCH", blocked=True)
    decision_at = _aware(frozen.get("claimed_decision_at"))
    quote_observed = _aware(frozen.get("quote_observed_at"))
    if (decision_at is None or quote_observed is None
            or not quote_observed <= decision_at <= known_at):
        return _fail(result, "PIT_SIGNED_FREEZE_CLOCK_HOLD", blocked=True)
    book1_claim = frozen.get("book1_event_sha256_claim")
    if not isinstance(book1_claim, str) or not _SHA.fullmatch(book1_claim):
        return _fail(result, "PIT_SIGNED_BOOK1_CLAIM_ABSENT")

    rows, journal = verified_native_book_journal(Path(journal_path))
    if journal.get("state") != "HASH_CHAIN_VERIFIED":
        return _fail(result, str(journal.get("reason", "PIT_BOOK_JOURNAL_UNVERIFIED")),
                     blocked=journal.get("state") == "HOLD")
    prior = [row for row in rows if tuple(row.get("canonical_identity") or ()) == identity
             and _aware(row.get("native_book_at")) <= decision_at
             and _aware(row.get("known_at")) <= decision_at]
    if not prior:
        return _fail(result, "PIT_SIGNED_BOOK1_NOT_KNOWN_BEFORE_DECISION")
    book1 = prior[-1]  # exactly the most recent known Book, never cherry-picked
    if book1.get("event_sha256") != book1_claim:
        return _fail(result, "PIT_SIGNED_BOOK1_FREEZE_HASH_MISMATCH", blocked=True)
    quote = {"book_at": frozen.get("quote_native_book_at"),
             "observed_at": frozen.get("quote_observed_at"),
             **(frozen.get("quote_used") if isinstance(frozen.get("quote_used"), dict) else {})}
    if not _book1_matches_quote(book1, quote):
        return _fail(result, "PIT_SIGNED_BOOK1_QUOTE_MISMATCH", blocked=True)
    if (decision_at - _aware(book1["native_book_at"])).total_seconds() > max_book1_age_seconds:
        return _fail(result, "PIT_SIGNED_BOOK1_STALE")
    result.update(book1_quote_bound=True, book1_event_sha256=book1_claim,
                  journal_chain_tip_sha256=journal.get("latest_sha256"))

    # The signed clock, not the self-described decision clock, is the Book2 cut.
    witness = causal_book_witness(
        Path(journal_path), family_group=family_group, identity=identity,
        decision_at=pinned["decision_known_at"],
        latency_seconds=latency_seconds, side=frozen["action"],
        decision_receipt_sha256=pinned.get("receipt_sha256"))
    if witness.get("journal_chain_tip_sha256") != journal.get("latest_sha256"):
        return _fail(result, "PIT_JOURNAL_CHANGED_BETWEEN_READS", blocked=True)
    if witness.get("state") == "BLOQUEADO":
        return _fail(result, str(witness.get("reason", "PIT_BOOK2_INVALID")), blocked=True)
    if witness.get("causal_clock_witness") is not True:
        return _fail(result, "PPI_BOOK_NOT_STRICTLY_AFTER_SIGNED_KNOWN_AT_PLUS_LATENCY")
    book2_sha = witness.get("journal_event_sha256")
    first = next((row for row in rows if row.get("event_sha256") == book2_sha), None)
    if first is None:
        return _fail(result, "PIT_BOOK2_JOURNAL_EVENT_MISSING", blocked=True)
    if (first.get("session_art") != book1.get("session_art")
            or known_at.astimezone(ART).date().isoformat() != book1.get("session_art")):
        return _fail(result, "PPI_BOOK2_CROSS_SESSION_HOLD", blocked=True)
    if (_aware(first.get("native_book_at")) <= _aware(book1.get("native_book_at"))
            or _aware(first.get("native_book_at")) <= known_at + timedelta(seconds=latency_seconds)
            or _aware(first.get("known_at")) <= known_at):
        return _fail(result, "PIT_BOOK2_SIGNED_CAUSAL_CUT_INVALID", blocked=True)
    result.update(book2_event_sha256=book2_sha,
                  book2_native_at=first["native_book_at"],
                  book2_observed_at=first["known_at"],
                  book2_after_signed_known_at=True,
                  book2_first_top_positive=bool(witness["first_book_has_positive_native_top_level"]))
    if not result["book2_first_top_positive"]:
        return _fail(result, "PPI_FIRST_BOOK2_TOP_DEPTH_ZERO_OR_ABSENT")
    return {**result, "state": "EVIDENCE_ONLY_NO_GO",
            "reason": "SIGNED_DECISION_BOOK2_CLOCK_PAIR_SOURCE_UNITS_TARIFFS_UNVERIFIED"}
