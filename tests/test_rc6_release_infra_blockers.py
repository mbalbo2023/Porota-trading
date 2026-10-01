from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEPLOY = (REPO / ".github" / "workflows" / "porota-deploy-v2-promote.yml").read_text(encoding="utf-8")


def test_provisional_runtime_provenance_is_written_before_long_validation():
    frozen = DEPLOY.index("RC6_FROZEN_PROVENANCE_PROVISIONAL=GREEN")
    provisional = DEPLOY.index("RC6_CURRENT_STATE_PROVISIONAL=GREEN")
    contract = DEPLOY.index("Full contract evidence reconciliation")
    final = DEPLOY.index("validation_status=VALIDATED_RUNTIME")
    assert frozen < provisional < contract < final
    assert '"validation_status":"DEPLOYED_VALIDATION_PENDING"' in DEPLOY


def test_byma_shared_market_directory_is_narrowly_owned_before_runtime_start():
    ownership = DEPLOY.index("RC6_BYMA_SHARED_PATH_WRITABLE_GUARD=GREEN")
    runtime = DEPLOY.index('MODE_OUTPUT="$(sudo -n env POROTA_COPY_OUTPUT=0')
    assert ownership < runtime
    assert 'test ! -L "$REPO/data/market"' in DEPLOY
    assert 'install -d -o 1000 -g 1000 -m 0750 "$REPO/data/market"' in DEPLOY
    assert "chown -R 1000:1000" not in DEPLOY[ownership-1200:ownership+200]


def test_frozen_candidate_identity_is_persisted_before_long_runtime_validation():
    early = DEPLOY.index("RC6_FROZEN_PROVENANCE_PROVISIONAL=GREEN")
    contract = DEPLOY.index("Full contract evidence reconciliation")
    assert early < contract
    block = DEPLOY[early-1200:contract]
    assert '"$REMOTE_DIR/porota-frozen-candidate.json"' in block
    assert '"$REPO/data/deploy/porota-frozen-candidate.json"' in block
