#!/usr/bin/env python3
"""Static TEST-READY preflight. Never touches Docker, broker, DB or network."""
from pathlib import Path
import re
import sys
import _version

ROOT=Path(__file__).resolve().parent
checks=[]
def check(name, ok, detail):
    checks.append((name,bool(ok),str(detail)))

check('version_rc4', _version.VERSION.startswith('17.0.0-rc4-'), _version.VERSION)
check('image_matches_version', _version.IMAGE == f'porota-trading-bot:{_version.VERSION}', _version.IMAGE)
check('mode_paper', _version.MODE == 'PRODUCTION_PAPER', _version.MODE)
check('execution_simulated', _version.EXECUTION == 'SIMULATED', _version.EXECUTION)
check('real_orders_blocked', _version.REAL_ORDER_CAPABILITY == 'BLOCKED', _version.REAL_ORDER_CAPABILITY)
check('baseline_source_sha', bool(re.fullmatch(r'[0-9a-f]{40}',_version.RELEASE_SOURCE_COMMIT)), _version.RELEASE_SOURCE_COMMIT)
check('baseline_zip_sha', bool(re.fullmatch(r'[0-9a-f]{64}',_version.OPERATIVE_BASE_SHA256)), _version.OPERATIVE_BASE_SHA256)

mode=(ROOT/'porota_mode_manager.py').read_text(encoding='utf-8')
check('split_observer', 'porota_production_observer' in mode and 'bv_paper_runtime.py' in mode, 'observer split')
check('split_dashboard', 'porota_production_dashboard' in mode and 'o_dashboard.py' in mode, 'dashboard split')
check('observer_read_only', '"--user", "botuser", "--read-only"' in mode, 'docker --read-only')
check('ppi_secret_read_only', 'ppi_production.json:ro' in mode, 'secret mount :ro')
check('orders_blocked_declared', '"PPI_ORDERS": "BLOCKED"' in mode, 'PPI_ORDERS BLOCKED')

compose=(ROOT/'docker-compose.yml').read_text(encoding='utf-8')
check('compose_not_claimed_canonical_paper', 'NO es el contrato canónico de PRODUCTION_PAPER' in compose, 'legacy compose labelled')
check('no_runtime_artifact_memory_ses', not (ROOT/':memory:.ses').exists(), ':memory:.ses absent')
rc4_contract_service=(ROOT/'systemd/porota-contract-evidence-rc4.service').read_text(encoding='utf-8')
rc4_contract_script=(ROOT/'scripts/porota_contract_evidence_trusted_rc4.sh').read_text(encoding='utf-8')
legacy_scraper='cn_ppi_authenticated_family_scraper_hf6'
check('rc4_contract_uses_trusted_collector_only', legacy_scraper not in rc4_contract_service and legacy_scraper not in rc4_contract_script and 'rc4_trusted_browser_contract_collector.py' in rc4_contract_script, 'trusted-device GET-only path')

for name,ok,detail in checks:
    print(f"{'GREEN' if ok else 'RED'}|{name}|{detail}")
failed=[x for x in checks if not x[1]]
print(f'RC4_PREFLIGHT={"GREEN" if not failed else "RED"}')
print(f'CHECKS={len(checks)} FAILED={len(failed)}')
raise SystemExit(0 if not failed else 2)
