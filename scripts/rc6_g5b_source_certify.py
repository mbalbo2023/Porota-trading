"""Read-only finished G5B certification; no launch, promotion or deployment."""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).absolute().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import rc6_architectural_gates as gates


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference', type=Path, required=True)
    parser.add_argument('--source-sha', required=True)
    parser.add_argument('--source-tree', required=True)
    parser.add_argument('--source-root', type=Path, default=ROOT)
    parser.add_argument('--source-repository', type=Path, default=ROOT)
    parser.add_argument('--evidence-root', type=Path, required=True)
    parser.add_argument('--json-out', type=Path, required=True)
    parser.add_argument('--artifact-root', type=Path)
    args = parser.parse_args(argv)
    try:
        require = gates.require
        require(args.reference.stat().st_size <= 1024 * 1024, 'G5B_SOURCE_REFERENCE_BOUND')
        reference = json.loads(args.reference.read_bytes())
        qualified = gates.verify_g5_artifact(reference, source_sha=args.source_sha, source_tree=args.source_tree,
            source_root=args.source_root, source_repository=args.source_repository, evidence_root=args.evidence_root)
        result = qualified.public_report()
        if args.artifact_root:
            result['immutable_artifact_writer_join'] = gates.validate_g5_writer_artifact(qualified,
                source_sha=args.source_sha, source_tree=args.source_tree, source_root=args.source_root,
                artifact_root=args.artifact_root)
        require(not os.path.lexists(args.json_out), 'G5B_NEW_CERTIFICATION_REPORT_REQUIRED')
        descriptor = os.open(args.json_out, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(gates.canonical(result)); stream.flush(); os.fsync(stream.fileno())
        print(json.dumps({'status': result['status'], 'scope': result['certification_scope'],
            'launch_authority_granted': False, 'deploy_authorization': 'NOT_GRANTED', 'real_orders_sent': 0}))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(json.dumps({'status': 'BLOCKED', 'reason': str(error).partition(':')[0],
            'launch_authority_granted': False, 'deploy_authorization': 'NOT_GRANTED', 'real_orders_sent': 0}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
