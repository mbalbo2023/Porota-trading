# POROTA TRADING — AGENT BOOTSTRAP

This file is a bootstrap, not the source of truth for runtime state.

## Mandatory first reads
Before changing Porota, every agent/chat MUST:
1. Read `ops/policy/porota-policy.yaml`.
2. Read the current machine-generated runtime state when available.
3. Identify the exact Git SHA and branch being changed.
4. Reconcile claims with GitHub Actions and runtime evidence. Never continue from chat memory alone.

## Non-negotiable operating rules
- Porota is PAPER/SHADOW only. Real orders are forbidden.
- FIX-FORWARD ONLY. Rollback is forbidden.
- GitHub MCP + GitHub Actions are the primary control plane.
- Interactive SSH/user terminal is BREAK-GLASS only. The user must not be required to copy commands for normal operation.
- Never use Codespaces.
- Never modify PPI Watch.
- Never write directly to the protected deploy branch or main for development work. Use an isolated branch and explicit promotion.
- Parallel development is the default when workstreams are independent and safe; shared runtime writers remain serialized.
- Parallel workstreams must converge into ONE frozen candidate, ONE final Predeploy V2 gate, ONE immutable artifact/digest and ONE consolidated production deploy.
- A candidate must pass pre-deploy integrity, dependency, import, runtime-smoke and PAPER-safety gates before it may modify the droplet.
- Test the exact artifact that will be deployed.
- No hand-maintained partial runtime file list is an acceptable source of artifact completeness.
- Every deploy attempt, success or failure, must perform safe Docker/cache/temp cleanup and report reclaimed space.
- Never delete or prune active containers, volumes, databases, bind-mounted data, PPI Watch assets, or referenced runtime images.
- Every new deployment failure class must produce a permanent regression guard or an explicit documented reason why no automated guard is possible.
- A GitHub Action marked FAILED must not leave an ambiguous partially promoted runtime. Promotion must have an explicit state and evidence.

## Change discipline
- Prefer small, isolated, reversible-in-code changes, but never runtime rollback.
- Governance/CI work must not alter the trading engine, broker integrations, market data, strategies, dashboard behavior, or runtime unless explicitly in scope.
- Do not create a new one-off workflow when an existing parameterized control-plane workflow can safely perform the operation.
- Keep evidence concise and machine-readable: SHA, image digest, mode, real_orders_sent, health, cleanup result, and failure signature.

## Accessibility invariant
If a normal recurring operation requires the user to open a terminal, copy/paste shell commands, or manage GitHub manually, treat that as an automation defect to be fixed.
