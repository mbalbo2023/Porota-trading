from pathlib import Path

SCRIPT = Path("ops/rc6_deploy_critical_control_plane.sh").read_text(encoding="utf-8")


def test_secrets_directory_owned_by_control_plane_user_and_private():
    assert 'install -d -o "$CONTROL_USER" -g "$CONTROL_GROUP" -m 0700 "$SECRETS"' in SCRIPT
    assert 'install -d -m 0700 "$SECRETS"' not in SCRIPT


def test_broker_capability_is_checked_as_runtime_user_before_systemd_install():
    access_check = 'sudo -n -u "$CONTROL_USER" test -r "$BROKER_HOST_KEY"'
    unit_install = 'install -m 0644 /tmp/porota-critical-github-proxy-rc6.service'
    assert access_check in SCRIPT
    assert SCRIPT.index(access_check) < SCRIPT.index(unit_install)


def test_paper_safety_invariant_remains_postflight():
    assert "test \"$state_post\" = 'ok|PRODUCTION_PAPER|0'" in SCRIPT
    assert "echo 'REAL_ORDERS_SENT=0'" in SCRIPT
