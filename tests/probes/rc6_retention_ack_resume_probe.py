"""Independent offline SIGKILL reproduction of an older archive ACK retry.

Uses only temporary roots and public native archive/publication APIs. No
production source, host process, provider or external archive is contacted.
"""
import argparse
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import signal
import socket
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--working-tree-dirty", action="store_true")
    args = parser.parse_args()
    root = args.repository_root.resolve()
    sys.path.insert(0, str(root))
    network_attempts = []

    def blocked_network(*_args, **_kwargs):
        network_attempts.append("NETWORK_ATTEMPT_BLOCKED")
        raise RuntimeError("OFFLINE_ACK_PROBE_NETWORK_FORBIDDEN")

    socket.socket.connect = blocked_network
    socket.create_connection = blocked_network
    from tests.test_rc6_convergence_persistence import publish
    from rc6_dynamic_universe.common import digest
    from rc6_shadow_runtime.persistence import EvidenceFiles, read_committed_generation
    from rc6_shadow_runtime.retention import EvidenceRetention

    def archive_until_ack(path, archive, generation, pipe):
        def fault(stage):
            if stage == "archive_before_publish_ack":
                pipe.send(stage)
                signal.pause()
        EvidenceRetention(path, archive_root=archive, fault_inject=fault).archive_generation(generation)

    names = ("rc6_shadow_runtime/persistence.py", "rc6_shadow_runtime/retention.py",
             "tests/test_rc6_convergence_persistence.py")
    hashes = lambda: {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in names}
    before = hashes()
    configuration = {"generations": 3, "archive_destination": "TEMP_PRIVATE_SEPARATE_ROOT",
        "fault_boundary": "archive_before_publish_ack", "intermediate_archive": "GENERATION_2",
        "retry": "IDENTICAL_GENERATION_1_ARCHIVE", "process_model": "fork+SIGKILL",
        "seed": "SYNTHETIC_ACK_RESUME_V1", "schema": "RC6_SHADOW_ARCHIVE_ACK_V2"}
    result = {"source_sha": args.source_sha, "source_tree": args.source_tree,
        "source_state": ("WORKTREE_NOT_FROZEN" if args.working_tree_dirty
                         else "COMMITTED_SNAPSHOT_WITH_HASHED_NATIVE_FIXTURE"),
        "source_files_sha256": before,
        "probe_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "configuration": configuration, "configuration_sha256": digest(configuration),
        "scope": "OFFLINE_SYNTHETIC_PAPER_SHADOW_ONLY",
        "custody": "TEMP_LOCAL_PRIVATE_FSYNC_NOT_WORM",
        "real_orders_sent": 0, "real_routes_used": []}
    with tempfile.TemporaryDirectory(prefix="rc6-retention-ack-review-") as directory:
        path, archive = Path(directory) / "evidence", Path(directory) / "archive"
        with EvidenceFiles(path) as files:
            first, second, third = (publish(files, number) for number in (1, 2, 3))
        generation1 = path / ("gen-" + first["pointer"]["generation_id"])
        generation2 = path / ("gen-" + second["pointer"]["generation_id"])
        context = multiprocessing.get_context("fork")
        parent, child = context.Pipe(duplex=False)
        process = context.Process(target=archive_until_ack, args=(path, archive, generation1, child))
        process.start()
        try:
            if not parent.poll(10):
                raise AssertionError("ARCHIVE_FAULT_BOUNDARY_NOT_REACHED")
            assert parent.recv() == configuration["fault_boundary"]
            os.kill(process.pid, signal.SIGKILL)
            process.join(10)
            assert process.exitcode == -signal.SIGKILL
        finally:
            if process.is_alive():
                process.kill(); process.join(10)
            parent.close(); child.close()
        ack1 = path / ("archive-ack-" + first["pointer"]["generation_id"] + ".json")
        assert not ack1.exists()
        receipt1 = json.loads((archive / (first["pointer"]["generation_id"] + ".receipt.json")).read_text())
        receipt2 = EvidenceRetention(path, archive_root=archive).archive_generation(generation2)
        assert receipt2["receipt_sequence"] == 2 and receipt2["previous_receipt_digest"] == digest(receipt1)
        retry_error = None
        try:
            retry = EvidenceRetention(path, archive_root=archive).archive_generation(generation1)
            assert retry == receipt1
        except ValueError as error:
            retry_error = str(error)
        checkpoint = json.loads((archive / "CHECKPOINT.json").read_text())
        current = read_committed_generation(path)
        result.update(killed_exit_code=process.exitcode,
            old_receipt_and_object_preserved=(archive / (first["pointer"]["generation_id"] + ".tar.gz")).is_file(),
            old_generation_preserved=generation1.is_dir(), old_ack_regenerated=ack1.exists(),
            retry_error=retry_error, checkpoint_receipt_count=checkpoint["receipt_count"],
            checkpoint_digest_is_receipt2=checkpoint["receipt_digest"] == digest(receipt2),
            immutable_receipt_files=len(list(archive.glob("*.receipt.json"))),
            current_pointer_unchanged=current["pointer"] == third["pointer"],
            current_cut=current["report"]["as_of"])
    result["source_files_after_sha256"] = hashes()
    result["source_changed_during_probe"] = before != result["source_files_after_sha256"]
    result["network_attempts"] = len(network_attempts)
    result["passed"] = (retry_error is None and result["old_ack_regenerated"]
        and result["checkpoint_receipt_count"] == result["immutable_receipt_files"] == 2
        and result["checkpoint_digest_is_receipt2"] and result["current_pointer_unchanged"]
        and not result["source_changed_during_probe"] and not network_attempts)
    print(json.dumps(result, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
