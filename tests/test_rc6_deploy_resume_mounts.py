from pathlib import Path
import copy
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import rc6_deploy_resume_mounts as mounts

ROOT = '/opt/porota-trading'
ALLOWED = tuple('source' + str(i) + '.py' for i in range(18))
EXPECTED = {p: 'a' * 64 for p in ALLOWED}
DATA = dict(Destination='/app/data', Source=ROOT+'/data', Type='bind', RW=True)

def fixture():
    return [DATA.copy()] + [dict(Destination='/app/'+p, Source=ROOT+'/'+p, Type='bind', RW=False) for p in ALLOWED]

class MountTests(unittest.TestCase):
    def validate(self, values, name='porota_production_dashboard'):
        return mounts.validate_mounts(values, name, ROOT, EXPECTED, ALLOWED)

    def test_exact_canonical_readonly_sources_only(self):
        self.assertEqual(self.validate(fixture()), sorted(ALLOWED))
        self.assertEqual(self.validate([DATA.copy()], 'porota_production_observer'), [])

    def test_writable_source_wrong_host_or_type_rejected(self):
        for key, value in [('RW', True), ('Source', '/tmp/source0.py'), ('Type', 'volume')]:
            rows=fixture(); rows[1][key]=value
            with self.assertRaises(RuntimeError): self.validate(rows)

    def test_extra_missing_duplicate_and_observer_source_rejected(self):
        variants=[fixture()[1:], fixture()[:-1], fixture()+[fixture()[1]],
                  fixture()+[dict(Destination='/app', Source=ROOT, Type='bind', RW=False)],
                  fixture()+[dict(Destination='/app/foreign.py', Source=ROOT+'/foreign.py', Type='bind', RW=False)]]
        for rows in variants:
            with self.assertRaises(RuntimeError): self.validate(rows)
        with self.assertRaises(RuntimeError): self.validate(fixture(), 'porota_production_observer')

    def test_unknown_contract_and_unmanifested_sources_rejected(self):
        with self.assertRaises(RuntimeError): mounts.validate_mounts(fixture(), 'porota_production_dashboard', ROOT, {}, ALLOWED)
        with self.assertRaises(RuntimeError): mounts.validate_mounts(fixture(), 'porota_production_dashboard', ROOT, EXPECTED, ALLOWED[:-1])

    def test_patch_changes_only_probe_mount_validation_and_compiles(self):
        before=mounts.resume.REMOTE_PROBE
        after=mounts.updated_probe()
        compile(after, '<probe>', 'exec')
        self.assertEqual(mounts.resume.REMOTE_PROBE, before)
        for check in ('RUNTIME_PROVENANCE_DRIFT','INSTALLED_FILE_DRIFT','READINESS_REGRESSION','OBSERVER_HEARTBEAT'):
            self.assertIn(check, after)
        self.assertIn('CONTAINER_MOUNT_HASH_DRIFT',after)

if __name__ == '__main__': unittest.main()
