# CHECKPOINT 00 — WS-CONTROLPLANE-17 — BOUNDED DEPLOY PROGRESS

Date: 2026-10-01
Mode: READ_ONLY audit
Base integration SHA: b01b28a937e9e30c5fcca29d5b794f3944486955
Branch: audit/ws-controlplane-17-bounded-deploy-progress-20261001

## Scope

Read-only deployment progress evidence only.

This workstream must not:
- deploy or restart runtime;
- mutate DB/systemd/Docker;
- clean Docker;
- touch PPI Watch;
- write product branch;
- compete with the active DEPLOY_OWNER.

## Purpose

Provide a short, bounded, independently resumable probe while the canonical Deploy V2 performs long stability/soak checks. This prevents an interactive chat from needing to hold or poll a long-lived GitHub/SSH stream.

The probe reads only:
- observer mode / real_orders_sent / process and session state;
- observer/dashboard container status, image and restart count;
- existence/timestamps of Zero-Known-Error evidence files;
- selected CURRENT_STATE_V2 fields when present;
- PPI Watch unit status without mutation.

All remote access is bounded by job timeout, SSH connect/keepalive limits and an outer timeout.
