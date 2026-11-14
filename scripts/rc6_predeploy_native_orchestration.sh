#!/usr/bin/env bash
# OFFHOST orchestration only. Does not authorize Docker, cleanup or Predeploy GO.
# The caller supplies run_native_proof; native ownership remains a separate gate.

rc6_native_session_cadence() {
  local rc6_session_log="${RUNNER_TEMP:?}/porota-native-session.log"
  local rc6_cadence_rc=0 rc6_session_rc=0
  local rc6_cadence_pid

  # Independent session overlaps cadence; fresh must finish before legacy.
  run_native_proof session python scripts/rc6_postdeploy_stress.py \
    --output /proof/session > "$rc6_session_log" 2>&1 &
  rc6_session_pid=$!

  # Launch in a separate shell context: calling a Bash function under
  # `if ! function` suppresses errexit inside all its internal commands.
  # Immediate wait preserves fresh -> legacy while capturing its real status.
  run_native_proof cadence-fresh python scripts/rc6_postdeploy_stress.py \
    --cadence-stage fresh --output /proof/cadence &
  rc6_cadence_pid=$!
  wait "$rc6_cadence_pid" || rc6_cadence_rc=$?
  if (( rc6_cadence_rc == 0 )); then
    run_native_proof cadence-legacy python scripts/rc6_postdeploy_stress.py \
      --cadence-stage legacy --output /proof/cadence &
    rc6_cadence_pid=$!
    wait "$rc6_cadence_pid" || rc6_cadence_rc=$?
  fi

  # Always join the session: no successful cadence can hide its failure.
  wait "$rc6_session_pid" || rc6_session_rc=$?
  rc6_session_pid=
  cat "$rc6_session_log"
  if (( rc6_cadence_rc != 0 || rc6_session_rc != 0 )); then
    return 79
  fi
}
