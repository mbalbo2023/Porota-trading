#!/usr/bin/env python3
from pathlib import Path

p=Path('ops/rc6_deploy_critical_control_plane.sh')
s=p.read_text(encoding='utf-8')
start=s.find('cleanup() {')
trap=s.find('trap cleanup EXIT')
if start < 0 or trap < 0 or trap < start:
    raise SystemExit('legacy cleanup/trap block not found')
end=trap+len('trap cleanup EXIT')
replacement='''cleanup_stage() {\n  sudo -n rm -rf "$REMOTE_STAGE" >/dev/null 2>&1 || true\n}\ntrap cleanup_stage EXIT'''
s=s[:start]+replacement+s[end:]
p.write_text(s,encoding='utf-8')
print('AUTO_ROLLBACK_BLOCK_REPLACED=YES')
