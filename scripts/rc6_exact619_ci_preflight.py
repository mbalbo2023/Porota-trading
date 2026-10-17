"""Supplemental exact-image CI session, never native acceptance evidence."""
import hashlib
from pathlib import Path
from scripts.porota_image_source_identity import verify_source_identity
from scripts.rc6_postdeploy_stress import GROUPS,ROOT,run
from rc6_shadow_runtime import persistence
identity=verify_source_identity("5baeae7b0478d61d2dea04e5dbee4e0c1a3af951","268009eb5fb2416fb92435ea828be7ad8d875965")
assert persistence._native_json_bound is not None
binary=Path(persistence._native_json_bound.__file__)
assert hashlib.sha256(binary.read_bytes()).hexdigest()=="e0eed2fc86dde099583e2635ae6c6e57b8a7f53124bb71a04bef11f026daac99"
assert all((ROOT/path).is_file() for paths in GROUPS.values() for path in paths)
assert (ROOT/"tests/test_rc6_shadow_native_bound.py").is_file()
assert (ROOT/"tests/test_rc6_shadow_shared_audit.py").is_file()
assert (ROOT/"tests/test_issue465_budget_adversarial.py").is_file()
print("RC6_619_EXACT_IMAGE_SOURCE_BINARY_ASSETS=GREEN|groups=8",flush=True)
result=run(Path("/work"))
assert result["status"]=="GREEN" and result["smoke"] is False
assert result["evidence_kind"]=="CI_EXACT_IMAGE_PREFLIGHT"
assert result["session"]["simulated_minutes"]==405 and len(result["scenarios"])==8
assert result["session"]["real_orders_sent"]==0 and result["session"]["provider_requests"]==0
print("RC6_619_EXACT_IMAGE_CI_PREFLIGHT=GREEN|native_acceptance=false",flush=True)
