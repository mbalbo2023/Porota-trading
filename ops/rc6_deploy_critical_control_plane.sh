#!/usr/bin/env bash
set -Eeuo pipefail

: "${SOURCE_SHA:?SOURCE_SHA required}"
: "${REMOTE_STAGE:?REMOTE_STAGE required}"

# This path is a PROTECTOR STAGE ONLY, NOT a cutover or deployment claim.
# Bootstrap order: protect the live legacy systemd unit BEFORE three newborn
# role receipts are possible. An unproven legacy CID is NEVER adopted.
custody_hold() {
  printf 'RC6_OCI_CUSTODY_HOLD79:%s\n' "$1" >&2
  exit 79
}

[[ "$SOURCE_SHA" =~ ^[0-9a-f]{40}$ ]] || custody_hold 'SOURCE_SHA_FORMAT'
CUSTODY_CLI=/usr/local/sbin/porota-rc6-oci-custody
GATEWAY_UNIT=/etc/systemd/system/porota-critical-approval-rc6.service
GATEWAY_SERVICE=porota-critical-approval-rc6.service

# Absence or non-root ownership stops before sudo, traps, Docker, systemd,
# temporary files, secrets or release symlinks are touched.
[[ -f "$CUSTODY_CLI" && ! -L "$CUSTODY_CLI" ]] || custody_hold 'TRUSTED_CLI_MISSING'
cli_meta="$(stat -c '%u:%h:%a' "$CUSTODY_CLI" 2>/dev/null)" || custody_hold 'TRUSTED_CLI_STAT'
case "$cli_meta" in 0:1:755|0:1:750|0:1:700|0:1:500) ;; *) custody_hold 'TRUSTED_CLI_OWNER' ;; esac
# Examine the still-running legacy process BEFORE asking about the NEW intent.
# The installed root helper must re-check this same MainPID under its own lock
# immediately before writing the protector; a user-space snapshot is not authority.
gateway_before="$(systemctl show --property=ActiveState,MainPID --no-pager "$GATEWAY_SERVICE" 2>/dev/null)" || custody_hold 'LEGACY_UNIT_UNREADABLE'
[[ "$gateway_before" == *'ActiveState=active'* ]] || custody_hold 'LEGACY_NOT_ACTIVE'
gateway_pid="$(printf '%s\n' "$gateway_before" | sed -n 's/^MainPID=//p')"
[[ "$gateway_pid" =~ ^[1-9][0-9]*$ ]] || custody_hold 'LEGACY_MAINPID_UNVERIFIED'

# Only a verified, root-owned helper may describe the signed attempt intent.
# The output is a bounded, non-secret tuple; parsing it never grants authority.
stage_intent="$(sudo -n "$CUSTODY_CLI" critical describe-stage-intent --paper-only --source-sha "$SOURCE_SHA")" || custody_hold 'TRUSTED_INTENT_UNAVAILABLE'
[[ ${#stage_intent} -le 320 && "$stage_intent" != *$'\n'* ]] || custody_hold 'TRUSTED_INTENT_FORMAT'
read -r marker cid_generation run_id run_attempt artifact_digest GATEWAY_CONTAINER extra <<< "$stage_intent"
[[ "$marker" == RC6_STAGE_INTENT_V1 && -z "${extra:-}" ]] || custody_hold 'TRUSTED_INTENT_SCHEMA'
[[ "$cid_generation" =~ ^[0-9a-f]{32}$ && "$run_id" =~ ^[1-9][0-9]*$ && "$run_attempt" =~ ^[1-9][0-9]*$ ]] || custody_hold 'TRUSTED_INTENT_IDENTITY'
[[ "$artifact_digest" =~ ^sha256:[0-9a-f]{64}$ ]] || custody_hold 'TRUSTED_ARTIFACT_DIGEST'
[[ "$GATEWAY_CONTAINER" == "porota_rc6_critical_g_$cid_generation" ]] || custody_hold 'TRUSTED_BIRTH_NAME'
stage_dir="$(mktemp -d /tmp/rc6-critical-unit.XXXXXXXX)" || custody_hold 'UNIT_STAGE_CREATE'
STAGED_GATEWAY_UNIT="$stage_dir/porota-critical-approval-rc6.service"
cleanup_preview() {
  rm -f -- "$STAGED_GATEWAY_UNIT"
  rmdir -- "$stage_dir"
}
trap cleanup_preview EXIT

cat > "$STAGED_GATEWAY_UNIT" <<UNIT
[Unit]
Description=POROTA RC6 critical Telegram approval gateway
Requires=docker.service porota-critical-github-proxy-rc6.service
After=docker.service porota-critical-github-proxy-rc6.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecCondition=/bin/sh -c 'echo RC6_OCI_CUSTODY_HOLD79:CRITICAL_CUTOVER_NOT_AUTHORIZED >&2; exit 79'
ExecStartPre=/bin/sh -c 'i=0; while [ ! -S /run/porota-critical-approval-rc6/github.sock ]; do i=\$((i+1)); [ \$i -lt 30 ] || exit 1; sleep 1; done'
# This is NOT a Docker birth. The independently installed root-owned helper
# refuses new critical start until an exact born CID + fresh external Telegram
# exclusivity attestation exists. If ExecCondition were removed accidentally,
# this helper must still HOLD79 before Docker unless all proofs are satisfied.
# PID1 only supervises the one-shot request; container health is proved through
# the separate 3-CID custody/runtime attestation, never ActiveState alone.
ExecStart=/usr/local/sbin/porota-rc6-oci-custody critical start-newborn --paper-only --source-sha $SOURCE_SHA --generation $cid_generation
Restart=no
RestartSec=5
TimeoutStartSec=60
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
UNIT

# Critical security boundary: install a FIXED root-owned helper separately,
# outside candidate checkout (approved hash, no symlink, mode 0755/0750), with
# a root-owned template allowlist and a restricted sudoers entry for this verb.
# The helper MUST treat the candidate path and its SHA as untrusted inputs:
# INDEPENDENT_TEMPLATE_ALLOWLIST_REQUIRED (do not bless the digest supplied
# by this script). Under its private lock it must reattest the old MainPID,
# refuse unit drop-ins, compare the approved template, stage atomically,
# daemon-reload and verify the effective HOLD79 unit without start/stop/restart.
# No 'sudo flock ... /bin/bash -s' and no arbitrary checkout shell as root.
preview_digest="$(sha256sum "$STAGED_GATEWAY_UNIT" | cut -d' ' -f1)"
[[ "$preview_digest" =~ ^[0-9a-f]{64}$ ]] || custody_hold 'UNIT_DIGEST_INVALID'
if ! sudo -n "$CUSTODY_CLI" critical stage-protector \
    --unit-template "$STAGED_GATEWAY_UNIT" \
    --candidate-sha256 "$preview_digest" \
    --expected-mainpid "$gateway_pid" \
    --source-sha "$SOURCE_SHA"; then
  custody_hold 'UNIT_STAGE_OR_EFFECTIVE_STATE_UNVERIFIED'
fi

# Read back EFFECTIVE systemd state, not just the source file or helper status.
[[ "$(systemctl show --property=FragmentPath --value "$GATEWAY_SERVICE")" == "$GATEWAY_UNIT" ]] || custody_hold 'FRAGMENT_PATH_CHANGED'
[[ -z "$(systemctl show --property=DropInPaths --value "$GATEWAY_SERVICE")" ]] || custody_hold 'DROP_IN_PATHS_UNVERIFIED'
condition="$(systemctl show --property=ExecCondition --value "$GATEWAY_SERVICE")"
[[ "$condition" == *RC6_OCI_CUSTODY_HOLD79* ]] || custody_hold 'CONDITION_NOT_PROTECTED'
[[ -z "$(systemctl show --property=ExecStop --value "$GATEWAY_SERVICE")" ]] || custody_hold 'STOP_DIRECTIVE_NOT_EMPTY'
effective="$(systemctl cat --no-pager "$GATEWAY_SERVICE")" || custody_hold 'EFFECTIVE_UNIT_UNREADABLE'
[[ "$effective" == *'ExecCondition=/bin/sh -c'* ]] || custody_hold 'EFFECTIVE_HOLD_MISSING'
[[ "$effective" != *'docker rm -f porota_critical_approval_rc6'* && "$effective" != *'docker stop -t 10 porota_critical_approval_rc6'* ]] || custody_hold 'LEGACY_NAME_MUTATOR'
[[ "$(systemctl show --property=ActiveState,MainPID --no-pager "$GATEWAY_SERVICE")" == "$gateway_before" ]] || custody_hold 'LEGACY_MAINPID_CHANGED'

echo 'CRITICAL_UNIT=STAGED_HOLD79'
echo 'LEGACY_CRITICAL_BIRTH=NOT_ADOPTED_BY_NAME'
echo 'LEGACY_GATEWAY_PROCESS=UNCHANGED_AT_STAGE'
echo 'CRITICAL_AVAILABILITY=GAP_IF_LEGACY_EXITS'
echo "CONTROL_PLANE_SOURCE_SHA=$SOURCE_SHA"
echo 'CONTROL_PLANE_PERMANENT_DEPLOY=NOT_ATTEMPTED'
