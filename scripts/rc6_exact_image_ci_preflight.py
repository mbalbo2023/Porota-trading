"""Supplemental CI preflight of the exact frozen image; never native evidence."""
from pathlib import Path
from scripts.porota_image_source_identity import verify_source_identity
from scripts.rc6_postdeploy_stress import GROUPS,ROOT,run
identity=verify_source_identity("5cb663231b52aee566dec0b3bf9c5d45684b98f5","2f1825afed2417e4d4d7a8f003fecc009708921f")
assert all((ROOT/path).is_file() for paths in GROUPS.values() for path in paths)
assert (ROOT/"tests/test_rc6_shadow_shared_audit.py").is_file()
assert (ROOT/"tests/test_issue465_budget_adversarial.py").is_file()
print("RC6_618_EXACT_IMAGE_TEST_ASSETS=GREEN|groups=8",flush=True)
result=run(Path("/work"))
assert result["status"]=="GREEN" and result["smoke"] is False
assert result["evidence_kind"]=="CI_EXACT_IMAGE_PREFLIGHT"
assert result["session"]["simulated_minutes"]==405 and len(result["scenarios"])==8
assert result["session"]["real_orders_sent"]==0 and result["session"]["provider_requests"]==0
print("RC6_618_EXACT_IMAGE_CI_PREFLIGHT=GREEN|native_acceptance=false",flush=True)
