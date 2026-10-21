"""One fail-closed operator suspension verdict for all RC6 backup producers."""
import json
from pathlib import Path


BACKUP_POLICY = Path(__file__).resolve().parent / "ops/policy/rc6-disk-housekeeping-v1.json"


def backups_suspended(policy_path=BACKUP_POLICY):
    try:
        path = Path(policy_path)
        if path.is_symlink():
            return True
        policy = json.loads(path.read_text(encoding="utf-8"))
        return policy["backups"]["suspended"] is not False
    except (OSError, ValueError, KeyError, TypeError):
        return True
