"""Exercise real SQLite file permissions in a disposable /tmp fixture.

Root with DAC override drops to a non-root UID; capless namespace root is
already subject to DAC. Only this fixture is chmod/chowned, never the checkout.
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import pwd
import subprocess
import sys
import tempfile


def permission_subprocess_options():
    if os.geteuid() != 0:
        return {}
    # Root inside a restricted user namespace can have no DAC override and
    # cannot map another UID. In that case kernel DAC already enforces 0555.
    capabilities = next((line.split(':', 1)[1].strip()
                         for line in Path('/proc/self/status').read_text().splitlines()
                         if line.startswith('CapEff:')), None)
    if capabilities is None:
        raise AssertionError("cannot establish effective DAC capabilities")
    if not int(capabilities, 16) & (1 << 1):  # CAP_DAC_OVERRIDE
        return {}
    account = pwd.getpwnam("nobody")
    if account.pw_uid == 0 or account.pw_gid == 0:
        raise AssertionError("nonprivileged test identity required")
    return {"user": account.pw_uid, "group": account.pw_gid,
            "extra_groups": []}


@contextmanager
def isolated_source(original, builder):
    """Clone only fixture bytes, not the source checkout or a host database."""
    with tempfile.TemporaryDirectory(prefix="porota-ledger-nonroot-", dir="/tmp") as name:
        root = Path(name)
        source = root / "source"
        scratch = root / "scratch"
        source.mkdir(mode=0o755)
        scratch.mkdir(mode=0o700)
        database = source / "observer.db"
        before = original.read_bytes()
        database.write_bytes(before)
        archive = root / "reader.zip"
        builder.build(archive)
        database.chmod(0o444)
        archive.chmod(0o444)
        source.chmod(0o555)
        identity = permission_subprocess_options()
        if identity:
            os.chown(scratch, identity['user'], identity['group'])
        root.chmod(0o755)
        try:
            yield database, scratch, archive, before
        finally:
            source.chmod(0o755)  # cleanup of this isolated fixture only


def real_direct_and_copy(archive, database, scratch):
    code = ("import json,os,runpy,sys;"
            "sys.path.insert(0,sys.argv[1]);"
            "m=runpy.run_path(sys.argv[1]);"
            "p=sys.argv[2]+'.permission-probe';"
            "denied=False;"
            "\ntry:\n fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)\n"
            "except PermissionError: denied=True\n"
            "else: os.close(fd);os.unlink(p)\n"
            "print(json.dumps({'euid':os.geteuid(),'write_denied':denied,"
            "'direct':m['collect'](sys.argv[2]),"
            "'copy':m['collect_copy'](sys.argv[2],sys.argv[3])}))")
    completed = subprocess.run(
        [sys._base_executable, "-I", "-S", "-c", code,
         str(archive), str(database), str(scratch)],
        cwd=database.parent.parent, capture_output=True, text=True, timeout=20,
        **permission_subprocess_options())
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["write_denied"] is True, "write_denied must be true on source fixture"
    return result["direct"], result["copy"]
