#!/usr/bin/env python3
"""RC6 host-level general backup entrypoint.

The implementation is intentionally outside Docker and excludes secrets by
default. It delegates to the audited v17 backup implementation.
"""
from pathlib import Path

from scripts.v17_host_general_backup import create_backup


def main() -> int:
    create_backup(Path('/opt/porota-trading'), Path('/opt/porota-backups'), include_secrets=False)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
