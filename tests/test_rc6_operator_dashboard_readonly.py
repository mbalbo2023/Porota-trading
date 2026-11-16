"""The internal probe exports status evidence, never HTML or credentials."""
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from scripts.rc6_operator_dashboard_readonly import CHILD, POSTCLOSE_ROUTES, ROUTES, SCHEMA, audit_host
from scripts import rc6_operator_dashboard_readonly as operator


class OperatorAuditTests(TestCase):
 def test_dashboard_probe_uses_exact_private_cid_without_rootful_docker(self):
    cid = "2" * 64
    context = SimpleNamespace(roles={"observer": "1" * 64, "dashboard": cid},
        identity=SimpleNamespace(socket="/run/user/1000/porota-rc6-docker.sock",
            environment=lambda: {"PATH": "/usr/bin:/bin"}))
    payload = {"schema": SCHEMA, "read_only": True, "audit_scope": "MARKET_CRITICAL",
               "source_status": "BOUNDED", "sources": [{"component": "PPI_MARKETDATA", "state": "NO_VERIFICADO"}],
               "routes": [{"path": path, "http": 200, "nav": True, "content": True}
                          for path in ROUTES]}
    def runner(argv, **kwargs):
        self.assertEqual(argv[:6], ["/usr/bin/docker", "--host",
            "unix:///run/user/1000/porota-rc6-docker.sock", "container", "exec", cid])
        self.assertEqual(argv[6:9], ["python", "-B", "-c"])
        self.assertEqual(kwargs["timeout"], 150)
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload))
    with patch.object(operator, "_rootless_context", return_value=context, create=True):
        result = audit_host(runner=runner)
    self.assertEqual(result["status"], "ROUTES_VERIFIED")

 def test_embedded_probe_compiles(self):
    compile(CHILD, "<dashboard-probe>", "exec")
    self.assertEqual(len(POSTCLOSE_ROUTES), 52)
    self.assertEqual(len(set(POSTCLOSE_ROUTES)), 52)
    self.assertIn("/trading/cauciones", POSTCLOSE_ROUTES)
    self.assertIn("/analitica/contrafactuales", POSTCLOSE_ROUTES)

 def test_sanitized_audit_receipt_has_exact_routes_and_no_page_content(self):
    context = SimpleNamespace(roles={"observer": "1" * 64,
                                     "dashboard": "2" * 64},
        identity=SimpleNamespace(socket="/run/user/1000/porota-rc6-docker.sock",
            environment=lambda: {"PATH": "/usr/bin:/bin"}))
    payload = {"schema": SCHEMA, "read_only": True, "audit_scope": "MARKET_CRITICAL", "source_status": "BOUNDED",
               "routes": [{"path": path, "http": 200, "nav": True, "content": True,
                           "secret": "must never leave host"} for path in ROUTES],
               "sources": [{"component": "PPI_MARKETDATA", "state": "VERDE",
                            "checked_age_seconds": 2, "last_success_age_seconds": 2,
                            "detail": "must never leave host"}],
               "source_sync_status": "BOUNDED", "history_cutoff_repair": "INCOMPLETE",
               "source_sync": [{"source": "PPI_PRODUCTION_HISTORY", "state": "AMARILLO",
                                "last_attempt_age_seconds": 100, "last_success_age_seconds": 200,
                                "detail": "must never leave host"}],
               "equity_exits": {"status": "BOUNDED", "sampled": 9, "truncated": False,
                                "reasons": {"EOD_PAPER": 8, "STOP_PAPER": 1,
                                            "private-reason": 99}, "secret": "must never leave host"},
               "paper_today": {"status": "BOUNDED", "art_date": "2026-10-15", "sampled": 12,
                               "opened_today": 5, "closed_today": 4, "wins_today": 2,
                               "invalid_net_pnl": 0, "families_closed": {"ACCIONES": 4, "private": 1},
                               "equity_close_reasons_today": {"EOD_PAPER": 3, "STOP_PAPER": 1},
                               "secret": "must never leave host"},
               "caucion_attempt": {"status": "BOUNDED", "sampled": 1, "latest_age_seconds": 42,
                                   "latest_today": True, "latest_result": "PLACED_SIMULATED",
                                   "has_paper_id": True, "today_attempts": 1,
                                   "worker_status": "BOUNDED", "worker_attempts_today": 1,
                                   "worker_latest_today": True, "worker_latest_result": "HOLD",
                                   "secret": "must never leave host"}}
    def runner(argv, **kwargs):
        assert argv[:7] == ["/usr/bin/docker", "--host",
                           "unix:///run/user/1000/porota-rc6-docker.sock",
                           "container", "exec", "2" * 64, "python"]
        assert kwargs["timeout"] == 150
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload))
    with patch.object(operator, "_rootless_context", return_value=context):
      result = audit_host(runner=runner)
    self.assertEqual(result["status"], "ROUTES_VERIFIED")
    self.assertEqual(len(result["routes"]), len(ROUTES))
    payload['routes'][0]['visible_status_counts']={'STALE':2,'NO_VERIFICADO':3,
        'secret-value':42,'ERROR':-1,'FRESH':True,'RUNNING':1001}
    with patch.object(operator, "_rootless_context", return_value=context):
      self.assertEqual(audit_host(runner=runner)['routes'][0]['visible_status_counts'],
                     {'STALE':2,'NO_VERIFICADO':3})
    self.assertEqual(result["source_sync"][0]["last_attempt_age_seconds"], 100)
    self.assertEqual(result["history_cutoff_repair"], "INCOMPLETE")
    self.assertEqual(result["equity_exits"], {"status": "BOUNDED", "sampled": 9,
                      "truncated": False, "reasons": {"EOD_PAPER": 8, "STOP_PAPER": 1}})
    self.assertEqual(result["paper_today"]["families_closed"], {"ACCIONES": 4})
    self.assertEqual(result["paper_today"]["wins_today"], 2)
    self.assertEqual(result["caucion_attempt"]["today_attempts"], 1)
    self.assertEqual(result["caucion_attempt"]["worker_latest_result"], "HOLD")
    self.assertNotIn("must never leave host", json.dumps(result))

    payload["profile_status"] = "BOUNDED"
    payload["profile"] = [{"stage": "balances", "elapsed_ms": 2832,
                           "read_queries": 4, "state": "READ_OK", "sql": "must never leave host"}]
    payload["balance_queries"] = [{"stage": "LATEST_EQUITY", "elapsed_ms": 2800,
                                    "state": "READ_OK", "sql": "must never leave host"}]
    with patch.object(operator, "_rootless_context", return_value=context):
      self.assertEqual(audit_host(runner=runner)["profile"][0]["elapsed_ms"], 2832)
      self.assertEqual(audit_host(runner=runner)["balance_queries"][0]["stage"], "LATEST_EQUITY")
      self.assertNotIn("must never leave host", json.dumps(audit_host(runner=runner)))

    payload["routes"][0]["elapsed_ms"] = 3100
    payload["routes"][0]["slow_2s"] = True
    payload["routes"][0]["unverified_mentions"] = 3
    with patch.object(operator, "_rootless_context", return_value=context):
      self.assertEqual(audit_host(runner=runner)["status"], "ROUTES_DEGRADED")
      self.assertEqual(audit_host(runner=runner)["routes"][0]["unverified_mentions"], 3)
    payload["audit_scope"] = "POSTCLOSE_ALL"
    payload["routes"] = [{"path": path, "http": 200, "nav": True, "content": True}
                         for path in POSTCLOSE_ROUTES]
    with patch.object(operator, "_rootless_context", return_value=context):
      self.assertEqual(audit_host(runner=runner)["audit_scope"], "POSTCLOSE_ALL")
      self.assertEqual(len(audit_host(runner=runner)["routes"]), 52)
    payload['identity_clocks']={'status':'BOUNDED','rows':[{
        'symbol':'GGAL','family':'ACCIONES','market':'BYMA','currency':'ARS','settlement':'A-24HS',
        'book_freshness':'STALE','trade_freshness':'STALE','receipt_freshness':'FRESH',
        'book_at':'2026-10-16T13:00:00+00:00','received_at':'api_token=private',
        'paper_id':'private-ledger-key','pnl':'private-amount'}]*11}
    with patch.object(operator, "_rootless_context", return_value=context):
      clocks=audit_host(runner=runner)['identity_clocks']
    self.assertEqual(len(clocks['rows']),10)
    self.assertEqual(clocks['rows'][0]['book_freshness'],'STALE')
    self.assertIsNone(clocks['rows'][0]['received_at'])
    self.assertNotIn('private',json.dumps(clocks))
    payload["history_cutoff_repair"] = "COMPLETE; secret"
    payload["source_sync"][0]["last_success_age_seconds"] = -1
    with patch.object(operator, "_rootless_context", return_value=context):
      redacted = audit_host(runner=runner)
    self.assertEqual(redacted["history_cutoff_repair"], "NO_VERIFICADO")
    self.assertIsNone(redacted["source_sync"][0]["last_success_age_seconds"])


 def test_partial_or_untrusted_receipt_fails_closed(self):
    payload = {"schema": SCHEMA, "read_only": True, "audit_scope": "MARKET_CRITICAL", "source_status": "BOUNDED",
               "routes": [{"path": "/", "http": 200, "nav": True, "content": True}],
               "sources": []}
    result = audit_host(runner=lambda *args, **kwargs:
                        SimpleNamespace(returncode=0, stdout=json.dumps(payload)))
    self.assertEqual(result["status"], "NO_VERIFICADO")
    self.assertEqual(result["routes"], [])


class ProductRootlessContextTests(TestCase):
 def setUp(self):
    self.temp = tempfile.TemporaryDirectory()
    self.addCleanup(self.temp.cleanup)
    self.home = Path(self.temp.name)
    self.state = self.home / ".local/state/porota-rc6"
    self.state.mkdir(parents=True)
    self.state.chmod(0o700)
    self.current_path = self.state / "current.json"
    self.socket = Path("/run/user/1000/porota-rc6-docker.sock")
    self.source_sha, self.tree_sha = "a" * 40, "b" * 40
    self.image = "sha256:" + "c" * 64
    self.cids = {"observer": "1" * 64, "dashboard": "2" * 64}
    self.current = {"schema": "porota.rootless.deploy.v1", "state": "VALIDADO_RUNTIME",
        "phase": "COMPLETE", "operating_mode": "PRODUCTION_PAPER",
        "execution_mode": "SIMULATION", "real_orders_sent": 0,
        "source_sha": self.source_sha, "source_tree": self.tree_sha,
        "image_id": self.image, "daemon_id": "private-daemon-1",
        "attempt": "43758003307-1", "roles": self.cids,
        "health": {"orders_guard": "BLOCKED"}, "private": "never-print-secret"}
    self.write_current()
    self.calls = []
    self.socket_mode = 0o660  # Physical private rootless socket: UID1000, mode 0660.
    self.socket_owner = 1000
    self.original_lstat = Path.lstat
    self.original_fstat = os.fstat

 def write_current(self):
    self.current_path.write_text(json.dumps(self.current))
    self.current_path.chmod(0o600)

 def lstat(self, path):
    if Path(path) == self.socket:
        return SimpleNamespace(st_mode=stat.S_IFSOCK | self.socket_mode,
                               st_uid=self.socket_owner, st_nlink=1, st_size=0)
    if Path(path) == self.socket.parent:
        return SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_uid=1000, st_nlink=1, st_size=0)
    row = self.original_lstat(path)
    return SimpleNamespace(st_mode=row.st_mode, st_uid=1000, st_nlink=row.st_nlink, st_size=row.st_size)

 def fstat(self, fd):
    row = self.original_fstat(fd)
    return SimpleNamespace(st_mode=row.st_mode, st_uid=1000, st_nlink=row.st_nlink, st_size=row.st_size)

 def run_command(self, command, **kwargs):
    command = tuple(command)
    self.calls.append(command)
    self.assertEqual(command[:3], ("/usr/bin/docker", "--host", f"unix://{self.socket}"))
    self.assertEqual(kwargs["timeout"], 8)
    self.assertEqual(kwargs["cwd"], "/")
    self.assertNotIn("DOCKER_HOST", kwargs["env"])
    if command[3:] == ("info", "--format", "{{json .}}"):
        payload = {"ID": "private-daemon-1", "SecurityOptions": ["name=rootless"],
            "DockerRootDir": str(self.home / ".local/share/porota-rc6/docker"),
            "CgroupVersion": "2", "CgroupDriver": "systemd", "private": "never-print-secret"}
    elif command[3:5] == ("container", "inspect") and command[5] in self.cids.values():
        cid = command[5]
        role = next(name for name, value in self.cids.items() if value == cid)
        payload = [{"Id": cid, "Name": f"/porota-rc6-{role}-43758003307-1",
            "Image": self.image, "State": {"Status": "running", "Running": True, "OOMKilled": False},
            "HostConfig": {"ReadonlyRootfs": True, "Privileged": False,
                "CapDrop": ["ALL"], "CapAdd": [], "SecurityOpt": ["no-new-privileges:true"],
                "NetworkMode": "porota-rc6-private", "PidMode": "",
                "Memory": 1024 ** 3, "NanoCpus": 10 ** 9, "PidsLimit": 256,
                "PortBindings": ({"8000/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8000"}]}
                                 if role == "dashboard" else {})},
            "Mounts": [{"Type": "bind", "Destination": "/app/data",
                         "Source": "/opt/porota-trading/data", "RW": True},
                        {"Type": "bind", "Destination": "/app/operator",
                         "Source": str(self.home / ".config/porota-rc6/operator"), "RW": False}]
                      + ([{"Type": "bind", "Destination": "/run/secrets/ppi_production.json",
                           "Source": "/opt/porota-trading/.secrets/ppi_production.json", "RW": False}]
                         if role == "observer" else []),
            "Config": {"Labels": {"io.porota.project": "rc6", "io.porota.role": role,
                       "io.porota.attempt": "43758003307-1", "private": "never-print-secret"},
                       "Image": self.image, "User": "0:0", "WorkingDir": "/app",
                       "Entrypoint": None,
                       "Cmd": ["python", "bf_production_paper_observer.py" if role == "observer" else "o_dashboard.py"],
                       "Env": ["POROTA_BUILD_SHA=" + self.source_sha,
                               "POROTA_CANDIDATE_TREE_SHA=" + self.tree_sha,
                               "PAPER_V17_DB_PATH=/app/data/paper_v17/observer_v17.db",
                               "DASHBOARD_OPERATION_MODE=PRODUCTION_PAPER",
                               "EXECUTION_MODE=SIMULATION", "REAL_ORDER_CAPABILITY=BLOCKED",
                               "TOKEN=never-print-secret"]}}]
    else:
        self.fail("Unexpected or mutating Docker command: " + repr(command))
    return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="never-print-secret")

 def context(self):
    with patch.object(operator.os, "getuid", return_value=1000), \
         patch.object(operator.os, "geteuid", return_value=1000), \
         patch.object(operator.pwd, "getpwuid", return_value=SimpleNamespace(
             pw_uid=1000, pw_name="porotaadmin", pw_dir=str(self.home))), \
         patch.object(Path, "lstat", autospec=True, side_effect=self.lstat), \
         patch.object(operator.os, "fstat", side_effect=self.fstat):
        return operator._rootless_context(runner=self.run_command)

 def test_current_release_and_two_cids_are_verified_without_product_python_helper(self):
    # The deployed product contains no module.read_release_state. No helper
    # file is staged in this fixture; the bounded dashboard context must work.
    context = self.context()
    self.assertEqual(context.roles, self.cids)
    self.assertEqual(str(context.identity.socket), str(self.socket))
    self.assertEqual(context.current["source_sha"], self.source_sha)
    self.assertEqual(context.current["source_tree"], self.tree_sha)
    self.assertEqual(set(command[3:5] for command in self.calls),
                     {("info", "--format"), ("container", "inspect")})
    self.assertEqual(len(self.calls), 3)
    self.assertNotIn("never-print-secret", repr(context))

 def test_duplicate_cid_fails_before_docker_inspect(self):
    self.current["roles"] = {"observer": self.cids["observer"], "dashboard": self.cids["observer"]}
    self.write_current()
    with self.assertRaises(ValueError):
        self.context()
    self.assertEqual(self.calls, [])

 def test_private_socket_must_be_owner_owned_without_world_access(self):
    for mode, owner in ((0o666, 1000), (0o660, 1001)):
        self.socket_mode, self.socket_owner = mode, owner
        with self.subTest(mode=mode, owner=owner):
            with self.assertRaises(ValueError): self.context()
            self.assertEqual(self.calls, [])

 def test_wrong_image_or_observer_command_fails_closed(self):
    original = self.run_command
    for change in ("Image", "Cmd", "POROTA_BUILD_SHA", "REAL_ORDER_CAPABILITY"):
        self.calls.clear()
        def tampered(command, **kwargs):
            response = original(command, **kwargs)
            if tuple(command)[3:5] == ("container", "inspect") and command[5] == self.cids["observer"]:
                payload = json.loads(response.stdout)
                if change == "Image": payload[0]["Config"]["Image"] = "sha256:" + "d" * 64
                if change == "Cmd": payload[0]["Config"]["Cmd"] = ["python", "bv_paper_runtime.py"]
                if change == "POROTA_BUILD_SHA":
                    payload[0]["Config"]["Env"][0] = "POROTA_BUILD_SHA=" + "d" * 40
                if change == "REAL_ORDER_CAPABILITY":
                    payload[0]["Config"]["Env"][5] = "REAL_ORDER_CAPABILITY=ENABLED"
                response.stdout = json.dumps(payload)
            return response
        with self.subTest(change=change):
            self.run_command = tampered
            try:
                with self.assertRaises(ValueError): self.context()
            finally:
                self.run_command = original

 def test_product_isolation_mounts_and_loopback_port_must_match(self):
    original = self.run_command
    for change in ("ReadonlyRootfs", "Privileged", "CapDrop", "CapAdd",
                   "SecurityOpt", "NetworkMode", "PidMode", "Memory",
                   "NanoCpus", "PidsLimit", "Mounts", "PortBindings"):
        self.calls.clear()
        def tampered(command, **kwargs):
            response = original(command, **kwargs)
            if tuple(command)[3:5] == ("container", "inspect"):
                payload = json.loads(response.stdout)
                role = "observer" if command[5] == self.cids["observer"] else "dashboard"
                row = payload[0]
                host = row["HostConfig"]
                if change == "ReadonlyRootfs": host["ReadonlyRootfs"] = False
                if change == "Privileged": host["Privileged"] = True
                if change == "CapDrop": host["CapDrop"] = []
                if change == "CapAdd": host["CapAdd"] = ["SYS_ADMIN"]
                if change == "SecurityOpt": host["SecurityOpt"] = []
                if change == "NetworkMode": host["NetworkMode"] = "host"
                if change == "PidMode": host["PidMode"] = "host"
                if change == "Memory": host["Memory"] = 0
                if change == "NanoCpus": host["NanoCpus"] = 0
                if change == "PidsLimit": host["PidsLimit"] = 0
                if change == "Mounts" and role == "observer":
                    row["Mounts"][-1]["RW"] = True
                if change == "PortBindings" and role == "dashboard":
                    host["PortBindings"]["8000/tcp"][0]["HostIp"] = "0.0.0.0"
                response.stdout = json.dumps(payload)
            return response
        with self.subTest(change=change):
            self.run_command = tampered
            try:
                with self.assertRaises(ValueError): self.context()
            finally:
                self.run_command = original

 def test_any_duplicate_or_malformed_environment_fails_like_product_parser(self):
    original = self.run_command
    for injected in (("UNRELATED_FLAG=one", "UNRELATED_FLAG=two"),
                     ("invalid-lowercase=value",), ("ENV_WITHOUT_EQUALS",),
                     ("CONTROL=hidden\x00byte",)):
        self.calls.clear()
        def tampered(command, **kwargs):
            response = original(command, **kwargs)
            if tuple(command)[3:5] == ("container", "inspect") and command[5] == self.cids["observer"]:
                payload = json.loads(response.stdout)
                payload[0]["Config"]["Env"].extend(injected)
                response.stdout = json.dumps(payload)
            return response
        with self.subTest(injected=injected):
            self.run_command = tampered
            try:
                with self.assertRaises(ValueError): self.context()
            finally:
                self.run_command = original
