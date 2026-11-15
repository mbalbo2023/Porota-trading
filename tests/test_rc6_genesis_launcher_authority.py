"""R362 authorization at the GitHub REST boundary, without SSH or a live token."""

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import importlib
import importlib.util
import io
import json
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

from scripts import rc6_r295_host_bootstrap_guard as r295
from scripts import rc6_r340_trusted_root_env_controller as r340


REPO = "mbalbo2023/Porota-trading"
API = "https://api.github.com/repos/" + REPO
MAIN = "c" * 40
CUTOFF = 6872471009
LEASE_ID, GO_ID = 6900000001, 6900000002
GO_MARKER = "RC6_R362_LAUNCHER_GO_V1="
HOST_KEY = "AAAAC3NzaC1lZDI1NTE5AAAAIAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8g"
OTHER_KEY = "AAAAC3NzaC1lZDI1NTE5AAAAIAIDBAUGBwgJCgsMDQ4PEBESExQVFhcYGRobHB0eHyAh"
HOST_FINGERPRINT = "3b13435c2e35275b6642a717bce9b7b865512d0d9448949a324623b53e20cde8"
LEASE_MARKER = "RC6_R362_DEPLOY_OWNER_V1="
CONTROL_LEASE_MARKER = "RC6_R367_GENESIS_DEPLOY_OWNER_V1="
ADMISSION_SCOPE = {
    "operation": "PROMOTION_ADMISSION", "trusted_main_sha": MAIN,
    "candidate_sha": "b" * 40, "candidate_tree": "d" * 40,
    "host_identity_source": "PROTECTED_DO_PINS",
    "host_identity_sha256": HOST_FINGERPRINT, "nonce": "a" * 32,
}
SCOPE = {
    "operation": "install_genesis_launcher",
    "trusted_main_sha": MAIN,
    "launcher_target": "/usr/local/sbin/porota-rc6-host-installer-genesis",
    "launcher_sha256": "76eac14930a913ff7ed5c0720ccb5ff5bfc050b7bebe5b4725a76685ee67aaff",
    "host_identity_source": "PROTECTED_DO_PINS",
    "host_identity_sha256": HOST_FINGERPRINT,
}
# Examples of the exact owner documents. The release names the original acquisition.
LEASE = {**SCOPE, "status": "ACTIVE"}
RELEASE = {"status": "RELEASED", "lease_comment_id": LEASE_ID}


def utc(value):
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def go_document(expires_at):
    return {**SCOPE, "inventory_comment_id": 6881987698, "lease_comment_id": LEASE_ID,
            "expires_at": expires_at}


def encoded(marker, doc):
    return marker + json.dumps(doc, separators=(",", ":"))


def comment(number, issue, body, stamp):
    return {"id": number, "node_id": "IC_" + str(number), "body": body,
            "issue_url": f"{API}/issues/{issue}", "created_at": stamp, "updated_at": stamp,
            "user": {"id": 151845499, "login": "mbalbo2023", "type": "User"},
            "author_association": "OWNER"}


class Response(io.BytesIO):
    status = 200
    headers = {"Content-Type": "application/json; charset=utf-8"}


class AuthorityTest(unittest.TestCase):
    def setUp(self):
        module = "scripts.rc6_genesis_launcher_authority"
        self.assertIsNotNone(importlib.util.find_spec(module), "missing R362 authority callback")
        self.auth = importlib.import_module(module)
        self.now = datetime.now(timezone.utc).replace(microsecond=0)
        self.created = utc(self.now - timedelta(minutes=5))
        self.go = go_document(utc(self.now + timedelta(hours=1)))
        self.go_comment = comment(GO_ID, 1093, encoded(GO_MARKER, self.go), self.created)
        self.lc = comment(LEASE_ID, 980, encoded(LEASE_MARKER, LEASE),
                          utc(self.now - timedelta(minutes=10)))
        self.historical = {41: "## Alta DEPLOY_OWNER histórico", 42: "DEPLOY_OWNER liberado"}
        self.ledger = [comment(k, 980, v, self.created) for k, v in self.historical.items()]
        self.ledger += [comment(CUTOFF, 980, "cierre de código", self.created), self.lc]
        self.env = {"GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_REF": "refs/heads/main",
                    "GITHUB_REPOSITORY": REPO, "GITHUB_SHA": MAIN, "GITHUB_ACTOR": "mbalbo2023",
                    "GITHUB_TRIGGERING_ACTOR": "mbalbo2023", "GITHUB_ACTOR_ID": "151845499",
                    "BOOTSTRAP_ACTION": "install_genesis_launcher", "DO_USER": "porotaadmin",
                    "DO_HOST": "host.example", "DO_KNOWN_HOSTS": f"host.example ssh-ed25519 {HOST_KEY}",
                    "RC6_R295_OWNER_COMMENT_ID": str(GO_ID), "GITHUB_TOKEN": "opaque.test-token",
                    "RC6_EXPECTED_DO_ID": "12345678", "RC6_EXPECTED_DO_REGION": "nyc1"}
        self.change = lambda path, data: data

    def http(self, req, timeout):
        self.assertEqual(req.get_method(), "GET")
        parts = urlsplit(req.full_url)
        self.assertEqual((parts.scheme, parts.netloc), ("https", "api.github.com"))
        self.assertEqual(req.get_header("Authorization"), "Bearer " + self.env["GITHUB_TOKEN"])
        self.assertLessEqual(timeout, 10)
        prefix = "/repos/" + REPO
        self.assertTrue(parts.path.startswith(prefix + "/"))
        path = parts.path[len(prefix):]
        if path == "/git/ref/heads/main":
            data = {"ref": "refs/heads/main", "object": {"type": "commit", "sha": MAIN}}
        elif path == "/issues/980":
            data = {"id": 6216186179, "number": 980, "url": API + path,
                    "comments": len(self.ledger), "updated_at": self.created}
        elif path == "/issues/980/comments":
            import urllib.parse
            query = urllib.parse.parse_qs(parts.query)
            self.assertEqual(query["per_page"], ["100"])
            page = int(query["page"][0])
            data = self.ledger[(page - 1) * 100:page * 100]
        elif path == "/issues/comments/" + str(GO_ID):
            data = self.go_comment
        elif path == "/issues/comments/" + str(LEASE_ID):
            data = self.lc
        else:
            self.fail("Unexpected independent selector: " + path)
        data = self.change(path, deepcopy(data))
        raw = data if type(data) is bytes else json.dumps(data).encode()
        return Response(raw)

    @contextmanager
    def rest(self, head=MAIN):
        hashes = {i: hashlib.sha256(v.encode()).hexdigest() for i, v in self.historical.items()}
        with (patch("urllib.request.OpenerDirector.open", self.http),
              patch.object(r340, "_checkout_head", return_value=head),
              patch.object(self.auth, "LEGACY_EVENTS", hashes)):
            yield

    def verify(self):
        return self.auth.verify_authority(self.env)

    def verify_lease(self, scope, marker=CONTROL_LEASE_MARKER):
        return self.auth.verify_global_lease(
            self.auth._GitHub(self.env["GITHUB_TOKEN"]), self.lc, LEASE_ID,
            marker=marker, scope=scope)

    def test_admission_lease_is_recognized_without_bootstrap_authority(self):
        self.lc["body"] = encoded(CONTROL_LEASE_MARKER, {**ADMISSION_SCOPE, "status": "ACTIVE"})
        with self.rest():
            self.assertEqual(self.verify_lease(ADMISSION_SCOPE), (len(self.ledger), self.created))
            # Recognizing ownership cannot substitute for the launcher's own GO/lease.
            with self.assertRaisesRegex(r295.BootstrapHold, "LAUNCHER_GLOBAL_LEASE_MISMATCH"):
                self.verify()

    def test_control_leases_cannot_substitute_other_operations(self):
        operations = ("PREPARE_ENV", "APPLY_GENESIS", "PROMOTION_ADMISSION")
        for actual in operations:
            self.lc["body"] = encoded(CONTROL_LEASE_MARKER,
                                      {**ADMISSION_SCOPE, "operation": actual, "status": "ACTIVE"})
            for expected in operations:
                scope = {**ADMISSION_SCOPE, "operation": expected}
                with self.subTest(actual=actual, expected=expected), self.rest():
                    if actual == expected:
                        self.assertEqual(self.verify_lease(scope), (len(self.ledger), self.created))
                    else:
                        with self.assertRaisesRegex(r295.BootstrapHold, "LAUNCHER_GLOBAL_LEASE_MISMATCH"):
                            self.verify_lease(scope)

    def test_admission_lease_requires_a_closed_well_typed_scope(self):
        invalid = [{k: v for k, v in ADMISSION_SCOPE.items() if k != missing}
                   for missing in ADMISSION_SCOPE]
        invalid += [{**ADMISSION_SCOPE, "extra": False}]
        invalid += [{**ADMISSION_SCOPE, key: True} for key in ADMISSION_SCOPE]
        invalid += [{**ADMISSION_SCOPE, "operation": value}
                    for value in ("INSTALL", "UNKNOWN", None, [], {})]
        invalid += [{**ADMISSION_SCOPE, key: value} for key, value in (
            ("trusted_main_sha", "c" * 39), ("candidate_sha", "z" * 40),
            ("candidate_tree", "D" * 40), ("nonce", "a" * 31),
            ("host_identity_source", "ENV"), ("host_identity_sha256", "a" * 63))]
        for scope in invalid:
            # Change both inputs together: rejection must come from the contract.
            self.lc["body"] = encoded(CONTROL_LEASE_MARKER, {**scope, "status": "ACTIVE"})
            with self.subTest(scope=scope), self.rest(), \
                    self.assertRaisesRegex(r295.BootstrapHold, "LAUNCHER_GLOBAL_LEASE_MISMATCH"):
                self.verify_lease(scope)

    def test_admission_and_existing_leases_share_global_exclusion_and_exact_release(self):
        custodian_marker = "RC6_R367_CUSTODIAN_DEPLOY_OWNER_V1="
        custodian_scope = {"operation": "install_genesis_custodian", "trusted_main_sha": MAIN,
                           "custodian_target": "/usr/local/libexec/porota-rc6-custody/rc6-genesis-custodian",
                           "custodian_sha256": self.auth.CUSTODIAN_SHA256,
                           "host_identity_source": "PROTECTED_DO_PINS",
                           "host_identity_sha256": HOST_FINGERPRINT}
        others = ((LEASE_MARKER, SCOPE), (custodian_marker, custodian_scope),
                  (CONTROL_LEASE_MARKER, {**ADMISSION_SCOPE, "operation": "PREPARE_ENV"}),
                  (CONTROL_LEASE_MARKER, {**ADMISSION_SCOPE, "operation": "APPLY_GENESIS"}))
        history = self.ledger[:-1]
        for other in others:
            for admission_first in (True, False):
                admission = (CONTROL_LEASE_MARKER, ADMISSION_SCOPE)
                (previous_marker, previous_scope), (marker, scope) = (
                    (admission, other) if admission_first else (other, admission))
                # Different main/nonce must still conflict: this is global ownership.
                previous_scope = {**previous_scope, "trusted_main_sha": "e" * 40}
                if "nonce" in previous_scope:
                    previous_scope["nonce"] = "f" * 32
                opened = comment(LEASE_ID - 2, 980, encoded(previous_marker,
                                 {**previous_scope, "status": "ACTIVE"}), self.created)
                closed = comment(LEASE_ID - 1, 980, encoded(previous_marker,
                                 {"status": "RELEASED", "lease_comment_id": LEASE_ID - 2}), self.created)
                self.lc["body"] = encoded(marker, {**scope, "status": "ACTIVE"})
                for rows, released in (([opened], False), ([opened, closed], True)):
                    with self.subTest(other=other[1]["operation"], admission_first=admission_first,
                                      released=released), patch.object(self, "ledger", history + rows + [self.lc]), \
                            self.rest():
                        if released:
                            self.assertEqual(self.verify_lease(scope, marker), (len(self.ledger), self.created))
                        else:
                            with self.assertRaisesRegex(r295.BootstrapHold, "LAUNCHER_GLOBAL_LEASE_CONFLICT"):
                                self.verify_lease(scope, marker)

    def test_admission_revocation_and_malformed_or_duplicate_release_fail_closed(self):
        self.lc["body"] = encoded(CONTROL_LEASE_MARKER, {**ADMISSION_SCOPE, "status": "ACTIVE"})
        release = {"status": "RELEASED", "lease_comment_id": LEASE_ID}
        closed = comment(GO_ID + 1, 980, encoded(CONTROL_LEASE_MARKER, release), self.created)
        with patch.object(self, "ledger", self.ledger + [closed]), self.rest(), \
                self.assertRaisesRegex(r295.BootstrapHold, "LAUNCHER_ACTIVE_GLOBAL_OWNER_UNVERIFIED"):
            self.verify_lease(ADMISSION_SCOPE)
        malformed = [(CONTROL_LEASE_MARKER, {**release, "lease_comment_id": ident})
                     for ident in (LEASE_ID - 1, str(LEASE_ID), True)]
        malformed += [(LEASE_MARKER, release), (CONTROL_LEASE_MARKER, {**release, "extra": False})]
        for marker, document in malformed:
            wrong = comment(GO_ID + 1, 980, encoded(marker, document), self.created)
            with self.subTest(marker=marker, document=document), \
                    patch.object(self, "ledger", self.ledger + [wrong]), self.rest(), \
                    self.assertRaisesRegex(r295.BootstrapHold, "LAUNCHER_GLOBAL_RELEASE_AMBIGUOUS"):
                self.verify_lease(ADMISSION_SCOPE)
        duplicate = comment(GO_ID + 2, 980, encoded(CONTROL_LEASE_MARKER, release), self.created)
        with patch.object(self, "ledger", self.ledger + [closed, duplicate]), self.rest(), \
                self.assertRaisesRegex(r295.BootstrapHold, "LAUNCHER_GLOBAL_RELEASE_AMBIGUOUS"):
            self.verify_lease(ADMISSION_SCOPE)

    def test_custodian_needs_its_own_go_and_lease_and_honors_other_global_owners(self):
        custodian = "RC6_R367_CUSTODIAN_DEPLOY_OWNER_V1="
        control = "RC6_R367_GENESIS_DEPLOY_OWNER_V1="
        protected = "e" * 64
        scope = {"operation": "install_genesis_custodian", "trusted_main_sha": MAIN,
                 "custodian_target": "/usr/local/libexec/porota-rc6-custody/rc6-genesis-custodian",
                 "custodian_sha256": protected, "host_identity_source": "PROTECTED_DO_PINS",
                 "host_identity_sha256": HOST_FINGERPRINT}
        owner_go = {**scope, "inventory_comment_id": 6883049719, "lease_comment_id": LEASE_ID,
                    "expires_at": self.go["expires_at"]}
        control_scope = {"operation": "APPLY_GENESIS", "trusted_main_sha": MAIN,
                         "candidate_sha": "b" * 40, "candidate_tree": "c" * 40,
                         "nonce": "a" * 32, "host_identity_source": "PROTECTED_DO_PINS",
                         "host_identity_sha256": HOST_FINGERPRINT}
        self.env["BOOTSTRAP_ACTION"] = "install_genesis_custodian"
        with self.rest(), patch.object(self.auth, "CUSTODIAN_SHA256", protected, create=True):
            with self.assertRaises(r295.BootstrapHold):
                self.verify()  # The historical launcher GO and lease cannot authorize these bytes.
            self.go_comment["body"] = encoded("RC6_R367_CUSTODIAN_GO_V1=", owner_go)
            with self.assertRaises(r295.BootstrapHold):
                self.verify()
            self.lc["body"] = encoded(custodian, {**scope, "status": "ACTIVE"})
            self.assertIsNone(self.verify())
            for marker, other_scope in ((LEASE_MARKER, SCOPE), (control, control_scope)):
                opened = comment(LEASE_ID-2, 980, encoded(marker, {**other_scope, "status": "ACTIVE"}), self.created)
                closed = comment(LEASE_ID-1, 980, encoded(marker, {"status": "RELEASED",
                                                                    "lease_comment_id": LEASE_ID-2}), self.created)
                original = self.ledger[:-1]
                for rows, passes in (([opened], False), ([opened, closed], True),
                                     ([opened, {**closed, "body": closed["body"].replace(marker, custodian)}], False)):
                    with self.subTest(marker=marker, passes=passes), patch.object(
                            self, "ledger", original + rows + [self.lc]):
                        if passes:
                            self.assertIsNone(self.verify())
                        else:
                            with self.assertRaises(r295.BootstrapHold):
                                self.verify()
            with patch.dict(self.env, BOOTSTRAP_ACTION="install_genesis_launcher"), self.assertRaises(r295.BootstrapHold):
                self.verify()
            for field, value in (("custodian_sha256", "a" * 64), ("custodian_target", SCOPE["launcher_target"]),
                                 ("inventory_comment_id", 6881987698), ("host_identity_sha256", "c" * 64)):
                with self.subTest(field=field), patch.dict(self.go_comment, body=encoded(
                        "RC6_R367_CUSTODIAN_GO_V1=", {**owner_go, field: value})), self.assertRaises(r295.BootstrapHold):
                    self.verify()


    def test_validly_formed_new_do_pins_cannot_rebind_an_approved_host(self):
        for change in ({"RC6_EXPECTED_DO_ID": "12345679"}, {"RC6_EXPECTED_DO_REGION": "sfo3"},
                       {"DO_KNOWN_HOSTS": "host.example ssh-ed25519 " + OTHER_KEY},
                       {"DO_HOST": "elsewhere.example", "DO_KNOWN_HOSTS": "elsewhere.example ssh-ed25519 " + HOST_KEY}):
            with self.subTest(change=change), patch.dict(self.env, change), self.rest(), \
                    self.assertRaises(r295.BootstrapHold):
                self.verify()


    def test_host_fingerprint_can_be_prepared_offhost_without_any_private_key_or_token(self):
        pins = {k: self.env[k] for k in ("DO_HOST", "RC6_EXPECTED_DO_ID",
                                         "RC6_EXPECTED_DO_REGION", "DO_KNOWN_HOSTS")}
        self.assertEqual(self.auth.host_identity_sha256(pins), HOST_FINGERPRINT)
        import base64
        import hmac
        salt = bytes(range(20))
        digest = hmac.digest(salt, b"host.example", "sha1")
        hashed = "|1|" + base64.b64encode(salt).decode() + "|" + base64.b64encode(digest).decode()
        pins["DO_KNOWN_HOSTS"] = f"# ordinary known_hosts comment\n{hashed} ssh-ed25519 {HOST_KEY} label\n"
        self.assertEqual(self.auth.host_identity_sha256(pins), HOST_FINGERPRINT)
        with self.assertRaises(r295.BootstrapHold):
            self.auth.host_identity_sha256({**pins, "DO_HOST": "elsewhere.example"})
        alias = f"host.example,alias.example ssh-ed25519 {HOST_KEY}"
        self.assertEqual(self.auth.host_identity_sha256({**pins, "DO_KNOWN_HOSTS": alias}), HOST_FINGERPRINT)

    def test_ssh_fingerprint_uses_valid_host_keys_not_arbitrary_bytes(self):
        import base64
        prefix = lambda b: len(b).to_bytes(4, "big") + b
        for line in ("elsewhere.example ssh-ed25519 " + HOST_KEY,
                     "* ssh-ed25519 " + HOST_KEY, "host.example rsa " + HOST_KEY,
                     "host.example ssh-rsa " + HOST_KEY,
                     "host.example ssh-ed25519 " + base64.b64encode(b"ssh-ed25519" + bytes(32)).decode(),
                     "host.example ssh-ed25519 " + base64.b64encode(
                         prefix(b"ssh-ed25519") + prefix(bytes(32))).decode(),
                     "host.example ssh-dss " + HOST_KEY, "host.example ssh-ed25519 #",
                     "host.example ssh-ed25519 " + HOST_KEY + "\n@revoked host.example ssh-ed25519 " + HOST_KEY,
                     "-----BEGIN OPENSSH PRIVATE KEY-----", "x" * 32769):
            with self.subTest(line=line[:60]), self.assertRaises(r295.BootstrapHold):
                self.auth.host_identity_sha256({**self.env, "DO_KNOWN_HOSTS": line})


    def test_default_port_matches_openssh_literal_host_lookup(self):
        import subprocess
        import tempfile
        entry = f"[host.example]:22 ssh-ed25519 {HOST_KEY}\n"
        with tempfile.NamedTemporaryFile(mode="w+") as fixture:
            fixture.write(entry)
            fixture.flush()
            check = subprocess.run(["ssh-keygen", "-F", "host.example", "-f", fixture.name],
                                   capture_output=True, check=False)
        self.assertEqual(check.returncode, 1)
        with self.assertRaises(r295.BootstrapHold):
            self.auth.host_identity_sha256({**self.env, "DO_KNOWN_HOSTS": entry})

    def test_public_host_keys_are_set_sorted_and_self_describing(self):
        import base64
        def wire(*fields):
            return base64.b64encode(b"".join(len(f).to_bytes(4, "big") + f for f in fields)).decode()
        def digest(entries):
            return self.auth.host_identity_sha256({**self.env, "DO_KNOWN_HOSTS": "\n".join(entries)})
        current = self.env["DO_KNOWN_HOSTS"]
        modulus = (2 ** 2048 - 159).to_bytes(257, "big")
        rsa = "host.example ssh-rsa " + wire(b"ssh-rsa", b"\x01\x00\x01", modulus)
        curve = b"nistp256"
        ec = "host.example ecdsa-sha2-nistp256 " + wire(b"ecdsa-sha2-" + curve, curve, b"\x04" + bytes(range(64)))
        self.assertNotEqual(digest([current]), digest([current, rsa]))
        self.assertEqual(digest([rsa, current, ec]), digest([ec, current, rsa, ec]))
        weak = "host.example ssh-rsa " + wire(b"ssh-rsa", b"\x03", b"\x03")
        with self.assertRaises(r295.BootstrapHold):
            digest([weak])

    def test_the_go_and_lease_must_each_name_the_same_concrete_host_fingerprint(self):
        for which in (self.go_comment, self.lc):
            original = which["body"]
            try:
                for replaced in (original.replace(HOST_FINGERPRINT, "d" * 64),
                                 original.replace('"host_identity_sha256":"' + HOST_FINGERPRINT + '",', '')):
                    which["body"] = replaced
                    with self.rest(), self.assertRaises(r295.BootstrapHold):
                        self.verify()
            finally:
                which["body"] = original

    def test_current_owner_with_separate_earlier_acquisition_passes(self):
        with self.rest():
            self.assertIsNone(self.verify())

    def test_live_and_local_sha_and_operator_must_agree(self):
        with self.rest(head="b" * 40), self.assertRaises(r295.BootstrapHold):
            self.verify()
        for field, bad in (("GITHUB_ACTOR_ID", "3"), ("GITHUB_TRIGGERING_ACTOR", "other"),
                           ("GITHUB_REPOSITORY", "other/Porota-trading"),
                           ("GITHUB_EVENT_NAME", "push"), ("GITHUB_REF", "refs/heads/other"),
                           ("GITHUB_ACTOR", "other"), ("BOOTSTRAP_ACTION", "install_files"),
                           ("GITHUB_TOKEN", "opaque\rheader"), ("DO_USER", "other")):
            with self.subTest(field=field), patch.dict(self.env, {field: bad}), self.rest(), \
                    self.assertRaises(r295.BootstrapHold):
                self.verify()
        self.change = lambda path, obj: ({"ref": "refs/heads/main",
            "object": {"type": "commit", "sha": "e" * 40}} if path == "/git/ref/heads/main" else obj)
        with self.rest(), self.assertRaises(r295.BootstrapHold):
            self.verify()

    def test_go_scope_duplicates_multiline_identity_and_window(self):
        for field, value in (("launcher_target", "/tmp/other"), ("launcher_sha256", "a" * 64),
                             ("operation", "install_files"), ("trusted_main_sha", "d" * 40),
                             ("host_identity_source", "ENV"), ("inventory_comment_id", 1),
                             ("lease_comment_id", True), ("lease_comment_id", GO_ID),
                             ("expires_at", utc(self.now - timedelta(seconds=1))),
                             ("expires_at", utc(self.now + timedelta(hours=2)))):
            with self.subTest(field=field), self.rest(), self.assertRaises(r295.BootstrapHold):
                self.go_comment["body"] = encoded(GO_MARKER, {**self.go, field: value})
                self.verify()
        for text in (encoded(GO_MARKER, self.go) + "\nexplanation",
                     encoded(GO_MARKER, self.go).replace('{"operation":', '{"operation":"x","operation":')):
            self.go_comment["body"] = text
            with self.rest(), self.assertRaises(r295.BootstrapHold):
                self.verify()
        self.go_comment["body"] = encoded(GO_MARKER, self.go)
        for key, value in (("id", GO_ID - 1), ("author_association", "MEMBER"),
                           ("issue_url", API + "/issues/980"), ("updated_at", utc(self.now)),
                           ("user", {"login": "mbalbo2023", "id": 1, "type": "User"})):
            with self.subTest(key=key), patch.dict(self.go_comment, {key: value}), self.rest(), \
                    self.assertRaises(r295.BootstrapHold):
                self.verify()

    def test_full_ledger_conflict_revoke_and_substitution_never_authorize(self):
        for body in (encoded(LEASE_MARKER, LEASE), encoded(LEASE_MARKER, RELEASE),
                     "### R900 — DEPLOY_OWNER exclusivo temporal para otro cambio físico",
                     "**DEPLOY_OWNER** liberado por el titular",
                     "DEPLOY_OWNER: ACTIVE para un cambio nuevo",
                     "### Revoco el DEPLOY_OWNER\nStop", LEASE_MARKER + "{"):
            with self.subTest(body=body):
                self.ledger.append(comment(GO_ID + 1, 980, body, self.created))
                try:
                    with self.rest(), self.assertRaises(r295.BootstrapHold):
                        self.verify()
                finally:
                    self.ledger.pop()
        for target in ([v for v in self.ledger if v is not self.lc],
                       [self.ledger[1], self.ledger[-2], self.lc],
                       [*self.ledger[:-2], self.lc],
                       [*self.ledger, self.lc]):
            with self.subTest(ids=[v["id"] for v in target]):
                with patch.object(self, "ledger", target), self.rest(), self.assertRaises(r295.BootstrapHold):
                    self.verify()
        self.ledger[0]["body"] += " changed"
        with self.rest(), self.assertRaises(r295.BootstrapHold):
            self.verify()

    def test_harmless_write_owner_history_and_closed_competitor(self):
        lease = deepcopy(self.lc)
        lease["id"] = LEASE_ID - 2
        release = comment(LEASE_ID - 1, 980, encoded(LEASE_MARKER,
                          {"status": "RELEASED", "lease_comment_id": LEASE_ID - 2}), self.created)
        self.ledger[-1:-1] = [lease, release]
        self.ledger.append(comment(GO_ID + 1, 980, "### R361 — A726\nWRITE_OWNER\n"
                                   "Ownership status: ACTIVE\nSin DEPLOY_OWNER.", self.created))
        self.ledger.append(comment(GO_ID + 2, 980, "NO DEPLOY_OWNER.\n"
                                   "No se tomó DEPLOY_OWNER.\nDEPLOY_OWNER=NEVER_ACQUIRED", self.created))
        with self.rest():
            self.assertIsNone(self.verify())

    def test_paginated_ledger_and_count_cannot_hide_a_release(self):
        self.ledger[-1:-1] = [comment(LEASE_ID - 140 + i, 980, "avance", self.created)
                              for i in range(100)]
        with self.rest():
            self.assertIsNone(self.verify())
        self.ledger.append(comment(GO_ID + 1, 980, encoded(LEASE_MARKER, RELEASE), self.created))
        with self.rest(), self.assertRaises(r295.BootstrapHold):
            self.verify()
        self.change = lambda path, obj: obj[:1] if path == "/issues/980/comments" else obj
        with self.rest(), self.assertRaises(r295.BootstrapHold):
            self.verify()


    def test_operational_warning_must_not_be_interpreted_as_a_physical_lease(self):
        # These two phrases occur verbatim in the independently reconciled #980 history.
        for text in ("no se adquiere DEPLOY_OWNER ni autoridad física.",
                     "No adquirir DEPLOY_OWNER hasta GO técnico fresco."):
            self.ledger.append(comment(GO_ID + len(self.ledger), 980, text, self.created))
        with self.rest():
            self.assertIsNone(self.verify())


    def test_untyped_postcutoff_claims_and_revocations_hold_even_after_negative_clause(self):
        receipts = (
            "Desde ahora queda sin efecto la reserva del DEPLOY_OWNER de R362.",
            "El titular declara que queda reservado el DEPLOY_OWNER para la otra sesión.",
            "Sin DEPLOY_OWNER, pero desde ahora queda reservado el DEPLOY_OWNER para B.",
            "No DEPLOY_OWNER para A sino para B.",
            "No se tomó el DEPLOY_OWNER: queda sin efecto su reserva.",
        )
        for body in receipts:
            with self.subTest(body=body):
                self.ledger.append(comment(GO_ID + 1, 980, body, self.created))
                try:
                    with self.rest(), self.assertRaises(r295.BootstrapHold):
                        self.verify()
                finally:
                    self.ledger.pop()
        self.ledger.append(comment(GO_ID + 1, 980,
                                   "R361 únicamente código. Sin `DEPLOY_OWNER`.\n"
                                   "No se tomó DEPLOY_OWNER.", self.created))
        with self.rest():
            self.assertIsNone(self.verify())


    def test_non_owner_cannot_block_or_grant_a_lease_using_public_repo_comments(self):
        forged = comment(GO_ID + 1, 980, encoded(LEASE_MARKER, RELEASE), self.created)
        forged.update(author_association="NONE", user={"login": "other", "id": 42, "type": "User"})
        self.ledger.append(forged)
        with self.rest():
            self.assertIsNone(self.verify())
        self.ledger.remove(self.lc)
        forged["body"] = encoded(LEASE_MARKER, LEASE)
        with self.rest(), self.assertRaises(r295.BootstrapHold):
            self.verify()

    def test_explicit_global_decision_with_prefix_and_revocation_in_markdown_are_not_passive(self):
        for text in ("**R400:** ahora asumo el DEPLOY_OWNER, host completo",
                     "**Global physical lease / DEPLOY_OWNER acquired**",
                     "R390 — DEPLOY_OWNER GLOBAL ACTIVE durante despliegue",
                     "> `DEPLOY_OWNER` RELEASED (mismo titular)",
                     "GLOBAL_DEPLOY_OWNER=ACTIVE"):
            self.ledger.append(comment(GO_ID + 1, 980, text, self.created))
            try:
                with self.subTest(text=text), self.rest(), self.assertRaises(r295.BootstrapHold):
                    self.verify()
            finally:
                self.ledger.pop()


    def test_transport_faults_and_redirects_fail_as_hold_without_disclosing_output(self):
        from http.client import IncompleteRead
        from urllib.error import HTTPError
        for failure in (IncompleteRead(b"private bytes"), HTTPError(API, 302, "redirect", {}, None)):
            with self.subTest(failure=type(failure).__name__), self.rest(), \
                    patch("urllib.request.OpenerDirector.open", side_effect=failure), \
                    self.assertRaises(r295.BootstrapHold) as caught:
                self.verify()
            self.assertNotIn("private", str(caught.exception))

    def test_shortened_duplicate_json_or_changed_count_cannot_certify(self):
        def raw_duplicates(path, doc):
            if path.endswith("/comments") and "?" not in path:
                return b'[{"id":5,"id":6}]'
            return doc
        self.change = raw_duplicates
        with self.rest(), self.assertRaises(r295.BootstrapHold):
            self.verify()
        versions = 0
        def drift(path, doc):
            nonlocal versions
            if path == "/issues/980":
                versions += 1
                if versions > 1:
                    doc["comments"] += 1
            return doc
        self.change = drift
        with self.rest(), self.assertRaises(r295.BootstrapHold):
            self.verify()

    def test_go_and_lease_cross_checks_detect_drift_in_one_read(self):
        calls = 0
        def change(path, obj):
            nonlocal calls
            if path == "/issues/comments/" + str(GO_ID):
                calls += 1
                if calls > 1:
                    obj["body"] = encoded(GO_MARKER, {**self.go, "expires_at": utc(self.now + timedelta(minutes=2))})
            return obj
        self.change = change
        with self.rest(), self.assertRaises(r295.BootstrapHold):
            self.verify()
        self.change = lambda path, obj: ({**obj, "body": encoded(LEASE_MARKER, RELEASE)}
            if path == "/issues/comments/" + str(LEASE_ID) else obj)
        with self.rest(), self.assertRaises(r295.BootstrapHold):
            self.verify()
