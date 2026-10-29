#!/usr/bin/env python3
"""A186 exact host pre/post cleanup snapshot; READ-ONLY and fail closed."""
import hashlib
import json
from pathlib import Path
import sqlite3
import stat
import subprocess
import urllib.request

ROOT = Path('/opt/porota-trading')
MARKER = ROOT / 'data/deploy/CURRENT_STATE_V2.json'
DB = ROOT / 'data/paper_v17/observer_v17.db'
BARRIER = Path('/run/lock/porota-rc6-deploy-v2-attempt.json')
LOCK = Path('/run/lock/porota-rc6-deploy-v2.lock')
EXPECTED = {
    'porota_production_observer': (
        '4d556dccf938003ae9b6dfecf88de5c66e9bd399b5ba1db9323daa131a3d0e17',
        'sha256:0f98bf70ab27d5d7abf81eef0db7b8dadcf20e8fa78d8f2912f4694e2017cc4e'),
    'porota_production_dashboard': (
        '52b48a90f0fa1028233cecb29eacdf754105cb133eff797edcae37431fba6af8',
        'sha256:0f98bf70ab27d5d7abf81eef0db7b8dadcf20e8fa78d8f2912f4694e2017cc4e'),
    'porota_critical_approval_rc6': (
        '91f5f57684e936fa9b368f8eba8264bc3284dbcaf73be00b55953ebc1af689c4',
        'sha256:094b0f7485d835c43be4c8f519051beb21d0f4fbdfe817dd2eeb30ab341a06ba'),
}


def exact_droplet():
    client = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    observed = []
    for key in ('id', 'region'):
        with client.open('http://169.254.169.254/metadata/v1/' + key, timeout=3) as response:
            if response.status != 200:
                raise ValueError('DROPLET_METADATA_RED')
            observed.append(response.read(64).decode('ascii').strip())
    if tuple(observed) != ('594077619', 'nyc1'):
        raise ValueError('DROPLET_IDENTITY_RED')


def immutable_file(path, max_bytes):
    if path.is_symlink() or not path.is_file():
        raise ValueError('SOURCE_PATH_UNSAFE')
    st = path.stat()
    if st.st_uid != 0 or st.st_size < 2 or st.st_size > max_bytes or st.st_mode & 0o022:
        raise ValueError('SOURCE_OWNER_MODE_RED')
    return path.read_bytes()


def docker(*args):
    result = subprocess.run(['docker', *args], capture_output=True, text=True, timeout=12)
    if result.returncode or len(result.stdout) > 65536:
        raise ValueError('DOCKER_READ_RED')
    return result.stdout.strip()


def main():
    exact_droplet()
    if not LOCK.is_file() or LOCK.is_symlink():
        raise ValueError('HOST_LOCK_NOT_PINNED')
    st = LOCK.stat()
    if not stat.S_ISREG(st.st_mode) or st.st_uid != 0 or stat.S_IMODE(st.st_mode) != 0o600:
        raise ValueError('HOST_LOCK_MODE_RED')
    if BARRIER.exists() or BARRIER.is_symlink():
        raise ValueError('HOST_ATTEMPT_BARRIER_PRESENT')
    marker_bytes = immutable_file(MARKER, 65536)
    marker = json.loads(marker_bytes)
    expected_marker = {
        'candidate_sha': '0d2d685ebbf1bc24cccce7f5fe84a22828101655',
        'deploy_sha': 'a92aaaddee549559ce6d68f1b8ef9bff452b6771',
        'image_id': EXPECTED['porota_production_observer'][1],
        'validation_status': 'DEPLOYED_VALIDATION_PENDING',
        'mode': 'PRODUCTION_PAPER',
        'real_orders_sent': 0,
        'real_order_routes': 'NOT_CALLED',
        'ppi_watch_untouched': True,
    }
    if any(marker.get(key) != val for key, val in expected_marker.items()):
        raise ValueError('HOST_MARKER_MISMATCH')
    if DB.is_symlink():
        raise ValueError('DB_PATH_UNSAFE')
    with sqlite3.connect(DB.as_uri() + '?mode=ro', uri=True, timeout=2) as conn:
        conn.execute('PRAGMA query_only=ON')
        if conn.execute('SELECT mode,real_orders_sent FROM observer_state WHERE id=1').fetchone() != ('PRODUCTION_PAPER', 0):
            raise ValueError('PAPER_ZERO_RED')
    ids = docker('ps', '-aq', '--no-trunc').splitlines()
    if set(ids) != {cid for cid, _ in EXPECTED.values()}:
        raise ValueError('CONTAINER_SET_DRIFT')
    containers = []
    for name, (cid, image_id) in EXPECTED.items():
        data = json.loads(docker('inspect', name))[0]
        if (data['Id'] != cid or data['Image'] != image_id
                or data['State']['Running'] is not True):
            raise ValueError('RUNTIME_CONTAINER_DRIFT')
        containers.append((name, cid, image_id, data['State']['StartedAt'], data['RestartCount']))
    # Do not send ANY signal or query credentials: systemd unit states only.
    unit_file = subprocess.run(['systemctl', 'list-unit-files', '--no-legend', '--no-pager'],
                               capture_output=True, text=True, timeout=10, check=True)
    ppi_units = sorted(row.split()[0] for row in unit_file.stdout.splitlines()
                       if row.split() and 'ppi' in row.split()[0].lower()
                       and 'watch' in row.split()[0].lower())
    ppi = []
    for unit in ppi_units:
        state = subprocess.run(['systemctl','is-active',unit], capture_output=True,
                               text=True, timeout=5, check=False).stdout.strip()
        enabled = subprocess.run(['systemctl','is-enabled',unit], capture_output=True,
                                 text=True, timeout=5, check=False).stdout.strip()
        ppi.append((unit, state, enabled))
    report = {
        'marker_sha256': hashlib.sha256(marker_bytes).hexdigest(),
        'marker_candidate': marker['candidate_sha'],
        'containers': sorted(containers),
        'ppi_watch_units': ppi,
        'paper_mode': 'PRODUCTION_PAPER',
        'real_orders_sent': 0,
        'barrier': 'ABSENT',
        'droplet_id': '594077619',
    }
    print('RC6_A186_CLEANUP_SNAPSHOT=' + json.dumps(report, sort_keys=True, separators=(',', ':')))


if __name__ == '__main__':
    main()
