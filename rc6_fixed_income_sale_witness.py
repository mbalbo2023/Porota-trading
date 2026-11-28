"""Durable handoff from the PAPER exit supervisor to the registered EXIT reader."""
from __future__ import annotations

import os
from pathlib import Path
import stat

import rc6_paper_causal_decision_receipt as causal


KEY_FILE_ENV = "POROTA_FI_MARKET_SALE_WITNESS_KEY_FILE"
EXIT_READER_FD = "POROTA_FI_EXIT_READER_KEY_CHANNEL"
ACK_RECORDED = "CAUSAL_RECEIPT_ALREADY_COMMITTED_NO_REPLAY"
REPLAN = frozenset({
    "PPI_PIT_BOOK1_NOT_RETAINED",
    "PPI_PIT_BOOK2_SESSION_CHANGED",
    "PPI_NATIVE_RECEIPT_STALE",
    "IOL_FI_BOOK1_NOT_RETAINED",
    "IOL_FI_BOOK2_SESSION_CHANGED",
    "FI_MARKET_SALE_NATIVE_DEPTH_INVALID",
    "FI_MARKET_SALE_BOOK2_NOT_STRICTLY_AFTER_ACK",
})


def _signer_challenge(release, pid):
    return causal._json(["rc6.paper-fi.exit-reader.v1", release, pid]).encode()


class ExitReaderKeyChannel:
    """The supervisor pins only the public key from its actual EXIT child."""

    def __init__(self, model):
        self.model = model
        self.fd = None
        self.signer_pid = None
        self.key_sha256 = None

    def clear(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        self.signer_pid = self.key_sha256 = None
        causal.PINNED_RELEASE_SHA256 = None
        causal.PINNED_DECISION_SIGNER_KEY_SHA256 = None

    def launch(self, spawn, command, *, cwd):
        self.clear()
        read_fd, write_fd = os.pipe2(os.O_CLOEXEC | os.O_NONBLOCK)
        try:
            env = dict(os.environ, **{EXIT_READER_FD: str(write_fd)})
            child = spawn(command, cwd=cwd, env=env, pass_fds=(write_fd,))
        except BaseException:
            os.close(read_fd)
            raise
        finally:
            os.close(write_fd)
        self.fd = read_fd
        return child

    def poll(self, child):
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        if child is None or child.poll() is not None:
            self.clear()
            return
        if self.fd is None:
            return
        try:
            message = os.read(self.fd, 97)
        except BlockingIOError:
            return
        self.clear()
        if len(message) != 96:
            return
        try:
            public, signature = message[:32], message[32:]
            Ed25519PublicKey.from_public_bytes(public).verify(signature,
                _signer_challenge(self.model["release"], child.pid))
        except InvalidSignature:
            return
        self.key_sha256 = causal._sha(public)
        self.signer_pid = child.pid
        causal.PINNED_RELEASE_SHA256 = self.model["release"]
        causal.PINNED_DECISION_SIGNER_KEY_SHA256 = self.key_sha256

    def status(self):
        return {"state": "ACTIVE_PAPER" if self.key_sha256 else "SIGNER_PENDING",
                "model": self.model.get("quantity_model", "NORMALIZED_HISTORICAL_PPI_VN"),
                "variants": len(self.model["rows"]) if "rows" in self.model else None,
                "release_sha256": self.model["release"], "policy_sha256": self.model["policy"],
                "signer_key_sha256": self.key_sha256, "signer_pid": self.signer_pid}


def _start_exit_reader_signer(model, fd):
    """Create the private key only inside the EXIT reader's protected memory."""
    import ctypes
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    try:
        libc = ctypes.CDLL(None, use_errno=True)
        pr_get_dumpable, pr_set_dumpable = 3, 4
        if (not stat.S_ISFIFO(os.fstat(fd).st_mode)
                or libc.prctl(pr_set_dumpable, 0, 0, 0, 0) != 0
                or libc.prctl(pr_get_dumpable, 0, 0, 0, 0) != 0):
            raise ValueError("FI_EXIT_READER_SIGNER_CUSTODY_UNVERIFIED")
        signer = Ed25519PrivateKey.generate()
        public = signer.public_key().public_bytes(serialization.Encoding.Raw,
                                                  serialization.PublicFormat.Raw)
        challenge = _signer_challenge(model["release"], os.getpid())
        if os.write(fd, public + signer.sign(challenge)) != 96:
            raise ValueError("FI_EXIT_READER_KEY_CHANNEL_INCOMPLETE")
    finally:
        os.close(fd)
    causal.PINNED_RELEASE_SHA256 = model["release"]
    causal.PINNED_DECISION_SIGNER_KEY_SHA256 = causal._sha(public)
    return signer


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

    def __init__(self, store, key_file=None, *, exit_signer=None):
        self.store = store
        self.key_file = Path(key_file) if key_file is not None else None
        self.exit_signer = exit_signer

    def acknowledge(self, paper_id):
        with self.store.connect() as db:
            row = db.execute("""SELECT h.decision_key,a.signer_key_id FROM paper_fi_sale_witness_handoff h
                JOIN paper_positions p USING(paper_id) JOIN observer_state s ON s.id=1
                LEFT JOIN paper_causal_decision_receipts a ON a.decision_key=h.decision_key
                WHERE h.paper_id=? AND h.state='PENDING' AND p.status='OPEN'
                  AND s.mode='PRODUCTION_PAPER' AND s.real_orders_sent=0""", (paper_id,)).fetchone()
        if row is None:
            return None
        if row[1] and row[1] != causal.PINNED_DECISION_SIGNER_KEY_SHA256:
            replan(self.store, row[0], "FI_EXIT_READER_SIGNER_RESTARTED")
            return None
        result = causal.attest_after_commit(Path(self.store.path), decision_key=row[0],
            signing_key_path=self.key_file, expected_release_sha256=causal.PINNED_RELEASE_SHA256,
            expected_signer_key_sha256=causal.PINNED_DECISION_SIGNER_KEY_SHA256,
            exit_signer=self.exit_signer)
        if result["signature_created"] or result["reason"] == ACK_RECORDED:
            return row[0]
        raise ValueError(result["reason"])

    def witness_first_later_book(self, decision_key):
        from rc6_fixed_income_sale_authority import attest_market_sale_book2

        try:
            witness = attest_market_sale_book2(self.store.path, decision_key=decision_key,
                signing_key_path=self.key_file, release=causal.PINNED_RELEASE_SHA256,
                exit_signer=self.exit_signer)
        except ValueError as exc:
            reason = str(exc)
            if reason in REPLAN:
                replan(self.store, decision_key, reason)
                return
            if reason in {"NO_AUTHENTIC_NATIVE_RECEIPT", "IOL_FI_BOOK2_NOT_YET_CAPTURED"}:
                return
            raise
        with self.store.connect() as db:
            db.execute("""UPDATE paper_fi_sale_witness_handoff
                SET state='WITNESSED', witness_json=?, reason=NULL
                WHERE decision_key=? AND state='PENDING'""",
                (causal._json(witness), decision_key))


def exit_reader_witness_from_environment(store):
    """Only construct the observer with pre-existing independent release trust."""
    from rc6_fixed_income_sale_authority import POLICY_PIN, deployed_exit_signing_model

    model = deployed_exit_signing_model()
    if model is not None:
        fd = os.environ.pop(EXIT_READER_FD, "")
        if not fd.isdecimal() or int(fd) <= 2:
            raise ValueError("FI_EXIT_READER_KEY_CHANNEL_REQUIRED")
        return ExitReaderSaleWitness(store, exit_signer=_start_exit_reader_signer(model, int(fd)))
    path = os.getenv(KEY_FILE_ENV)
    if (path and causal._digest_or_none(os.getenv(POLICY_PIN))
            and causal._digest_or_none(causal.PINNED_RELEASE_SHA256)
            and causal._digest_or_none(causal.PINNED_DECISION_SIGNER_KEY_SHA256)):
        return ExitReaderSaleWitness(store, path)
    return None
