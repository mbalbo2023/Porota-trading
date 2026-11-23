"""Durable handoff from the PAPER exit supervisor to the registered EXIT reader."""
from __future__ import annotations

import os
from pathlib import Path

import rc6_paper_causal_decision_receipt as causal


KEY_FILE_ENV = "POROTA_FI_MARKET_SALE_WITNESS_KEY_FILE"
ACK_RECORDED = "CAUSAL_RECEIPT_ALREADY_COMMITTED_NO_REPLAY"
REPLAN = frozenset({
    "PPI_PIT_BOOK1_NOT_RETAINED",
    "PPI_PIT_BOOK2_SESSION_CHANGED",
    "PPI_NATIVE_RECEIPT_STALE",
    "FI_MARKET_SALE_NATIVE_DEPTH_INVALID",
    "FI_MARKET_SALE_BOOK2_NOT_STRICTLY_AFTER_ACK",
})


def init_schema(connection):
    connection.execute("""CREATE TABLE IF NOT EXISTS paper_fi_sale_witness_handoff(
        paper_id TEXT PRIMARY KEY, decision_key TEXT NOT NULL UNIQUE,
        scope_sha256 TEXT NOT NULL, state TEXT NOT NULL,
        witness_json TEXT, reason TEXT,
        FOREIGN KEY(decision_key) REFERENCES paper_causal_decision_freezes(decision_key),
        CHECK(state IN ('PENDING', 'WITNESSED', 'REPLAN')),
        CHECK((state='WITNESSED') = (witness_json IS NOT NULL))
    )""")


def sale_scope(intent):
    return causal._sha(causal._json([intent[field] for field in (
        "identity", "cause", "maximum_vn", "policy_sha256")]))


def stage_in_decision_transaction(connection, decision_key, intent):
    """Stage the pointer in the same transaction as the immutable decision."""
    connection.execute("""INSERT INTO paper_fi_sale_witness_handoff
        VALUES(?, ?, ?, 'PENDING', NULL, NULL)
        ON CONFLICT(paper_id) DO UPDATE SET decision_key=excluded.decision_key,
            scope_sha256=excluded.scope_sha256, state='PENDING',
            witness_json=NULL, reason=NULL
        WHERE paper_fi_sale_witness_handoff.scope_sha256 != excluded.scope_sha256
           OR (paper_fi_sale_witness_handoff.state='REPLAN'
               AND paper_fi_sale_witness_handoff.decision_key != excluded.decision_key)""",
        (intent["paper_id"], decision_key, sale_scope(intent)))


def active_decision(store, paper_id, scope):
    with store.connect() as db:
        row = db.execute("""SELECT decision_key FROM paper_fi_sale_witness_handoff
            WHERE paper_id=? AND scope_sha256=? AND state!='REPLAN'""",
            (paper_id, scope)).fetchone()
    return row[0] if row else None


def read_witness(store, decision_key):
    """The supervisor receives signed evidence only, with no signing authority."""
    from rc6_fixed_income_runtime import _json

    with store.connect() as db:
        row = db.execute("""SELECT witness_json FROM paper_fi_sale_witness_handoff
            WHERE decision_key=? AND state='WITNESSED'""", (decision_key,)).fetchone()
    return _json(row[0]) if row and len(row[0]) <= causal.MAX_RECEIPT_BYTES * 2 else None


def replan(store, decision_key, reason):
    """Retire only this decision. Its frozen decision and ACK remain in the journal."""
    with store.connect() as db:
        db.execute("""UPDATE paper_fi_sale_witness_handoff
            SET state='REPLAN', witness_json=NULL, reason=?
            WHERE decision_key=? AND state!='REPLAN'""", (reason, decision_key))


class ExitReaderSaleWitness:
    """Only the separately running, gateway-registered exit_reader uses this writer."""

    def __init__(self, store, key_file):
        self.store = store
        self.key_file = Path(key_file)

    def acknowledge(self, paper_id):
        with self.store.connect() as db:
            row = db.execute("""SELECT h.decision_key FROM paper_fi_sale_witness_handoff h
                JOIN paper_positions p USING(paper_id) JOIN observer_state s ON s.id=1
                WHERE h.paper_id=? AND h.state='PENDING' AND p.status='OPEN'
                  AND s.mode='PRODUCTION_PAPER' AND s.real_orders_sent=0""", (paper_id,)).fetchone()
        if row is None:
            return None
        result = causal.attest_after_commit(Path(self.store.path), decision_key=row[0],
            signing_key_path=self.key_file, expected_release_sha256=causal.PINNED_RELEASE_SHA256,
            expected_signer_key_sha256=causal.PINNED_DECISION_SIGNER_KEY_SHA256)
        if result["signature_created"] or result["reason"] == ACK_RECORDED:
            return row[0]
        raise ValueError(result["reason"])

    def witness_first_later_book(self, decision_key):
        from rc6_fixed_income_sale_authority import attest_market_sale_book2

        try:
            witness = attest_market_sale_book2(self.store.path, decision_key=decision_key,
                signing_key_path=self.key_file, release=causal.PINNED_RELEASE_SHA256)
        except ValueError as exc:
            reason = str(exc)
            if reason in REPLAN:
                replan(self.store, decision_key, reason)
                return
            if reason == "NO_AUTHENTIC_NATIVE_RECEIPT":
                return
            raise
        with self.store.connect() as db:
            db.execute("""UPDATE paper_fi_sale_witness_handoff
                SET state='WITNESSED', witness_json=?, reason=NULL
                WHERE decision_key=? AND state='PENDING'""",
                (causal._json(witness), decision_key))


def exit_reader_witness_from_environment(store):
    """Only construct the observer with pre-existing independent release trust."""
    from rc6_fixed_income_sale_authority import POLICY_PIN

    path = os.getenv(KEY_FILE_ENV)
    if (path and causal._digest_or_none(os.getenv(POLICY_PIN))
            and causal._digest_or_none(causal.PINNED_RELEASE_SHA256)
            and causal._digest_or_none(causal.PINNED_DECISION_SIGNER_KEY_SHA256)):
        return ExitReaderSaleWitness(store, path)
    return None
