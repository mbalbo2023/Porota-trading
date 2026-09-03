#!/usr/bin/env python3
"""Generate an auditable lifecycle map for Porota source modules.

Static analysis cannot prove that dynamically imported code is dead, so the
output deliberately uses ORPHAN_REVIEW rather than deleting anything.  Shell,
systemd, Docker and workflow references are considered entrypoints too.
"""
from __future__ import annotations

import argparse
import ast
from collections import defaultdict
from dataclasses import dataclass
import json
from pathlib import Path
import re


@dataclass(frozen=True)
class Override:
    status: str
    reason: str


OVERRIDES = {
    "r_clear_kill_switch": Override("ACTIVE_TOOLING_MANUAL", "Manual safety command; intentionally never called by the bot."),
    "q_backtest": Override("DEPRECATED_RETAINED", "Source docstring retires it in favor of bx_execution_replay; retained for audit history."),
    "aq_macro_backtest": Override("ACTIVE_TOOLING_OFFLINE", "Offline macro-filter analysis, not runtime admission."),
    "ap_api_verifier": Override("ACTIVE_TOOLING_MANUAL", "Operator-run end-to-end API verifier."),
    "bj_sandbox_health_probe": Override("ACTIVE_TOOLING_MANUAL", "Sandbox-only health probe; production runtime must not call it."),
    "bh_paper_gemini": Override("COMPATIBILITY_INACTIVE", "Intraday AI is OFF in RC4; retained only until dependency cleanup is separately audited."),
    "cr_pending_settlement_diagnostics_hf6": Override("RC4_INTEGRATION_PENDING", "Read-only settlement diagnostics to be surfaced in dashboard."),
    "co_market_sessions_hf6": Override("RC4_INTEGRATION_PENDING", "WIP per-family session model; must be wired or explicitly deferred before release."),
    "cs_postclose_history_scheduler_hf6": Override("RC4_INTEGRATION_PENDING", "Audit-confirmed orphan; History Store v2 wiring work item."),
    "ct_ppi_history_salvage_hf6": Override("RC4_INTEGRATION_PENDING", "Audit-confirmed orphan; should connect proven PPI history to v2 sink."),
    "cz_a3_cem_public_history_hf6": Override("RC4_INTEGRATION_PENDING", "A3 CEM background/history client awaiting scheduled wiring."),
    "db_a3_cem_normalizer_hf6": Override("RC4_INTEGRATION_PENDING", "A3 CEM normalizer awaiting scheduled wiring."),
    "di_caucion_cash_sweep_runtime_hf6": Override("HOLD_FEATURE_UNWIRED", "Caucion cash sweep remains fail-closed until contract/cutoff evidence is verified."),
    "cy_a3_contract_bridge_hf6": Override("RC4_INTEGRATION_PENDING", "Contract Evidence enrichment bridge awaiting explicit scheduler wiring."),
    "cy_market_source_arbitration_hf6": Override("POLICY_LIBRARY_PENDING", "Background source policy; must be imported by the eventual A3/history job or deferred."),
    "co_contract_ingestion_policy_hf6": Override("RC4_INTEGRATION_PENDING", "Contract Evidence cadence policy awaiting scraping scheduler integration."),
    "cq_contract_readiness_hf6": Override("RC4_INTEGRATION_PENDING", "Readiness rules must be called by the canonical Contract Evidence reconciliation path."),
}

TEXT_ROOTS = ("scripts", "deploy", ".github")
TEXT_FILES = ("Dockerfile", "docker-compose.yml", "docker-compose.yaml", "entrypoint.sh")


def _docstring(path: Path) -> str:
    try:
        tree=ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        return ast.get_docstring(tree) or ""
    except (OSError, SyntaxError):
        return ""


def _imports(path: Path, known: set[str]) -> set[str]:
    try:
        tree=ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError):
        return set()
    result=set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name=alias.name.split(".")[0]
                if name in known:
                    result.add(name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            name=node.module.split(".")[0]
            if name in known:
                result.add(name)
    return result


def _text_entrypoints(root: Path, modules: set[str]) -> dict[str, set[str]]:
    refs=defaultdict(set)
    candidates=[]
    for folder in TEXT_ROOTS:
        base=root/folder
        if base.exists():
            candidates.extend(p for p in base.rglob("*") if p.is_file())
    candidates.extend(root/name for name in TEXT_FILES if (root/name).is_file())
    for path in candidates:
        try:
            text=path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel=str(path.relative_to(root))
        for module in modules:
            if re.search(rf"(?<![A-Za-z0-9_]){re.escape(module)}(?:\.py)?(?![A-Za-z0-9_])", text):
                refs[module].add(rel)
    return refs


def analyze(root: Path) -> list[dict]:
    root=root.resolve()
    files={p.stem:p for p in root.glob("*.py") if p.is_file()}
    modules=set(files)
    production_inbound=defaultdict(set)
    test_inbound=defaultdict(set)
    for source_name, path in files.items():
        is_test=source_name.startswith("test_")
        for target in _imports(path, modules):
            if target == source_name:
                continue
            (test_inbound if is_test else production_inbound)[target].add(path.name)
    external=_text_entrypoints(root, modules)
    rows=[]
    for module,path in sorted(files.items()):
        doc=_docstring(path)
        override=OVERRIDES.get(module)
        if module.startswith("test_"):
            status,reason="TEST_ONLY","Root-level test file; should be moved under tests/ or intentionally retained."
        elif override:
            status,reason=override.status,override.reason
        elif production_inbound[module]:
            status,reason="ACTIVE_RUNTIME_LIBRARY","Imported by production Python modules."
        elif external[module]:
            if any(ref.startswith("deploy/systemd/") for ref in external[module]):
                status,reason="ACTIVE_SCHEDULED_ENTRYPOINT","Referenced by a versioned systemd unit."
            elif any(ref in {"Dockerfile","docker-compose.yml","docker-compose.yaml","entrypoint.sh"} for ref in external[module]):
                status,reason="ACTIVE_RUNTIME_ENTRYPOINT","Referenced by Docker/application entrypoint configuration."
            else:
                status,reason="ACTIVE_TOOLING_ENTRYPOINT","Referenced by scripts/workflows but not imported by runtime Python."
        elif any(word in doc.upper() for word in ("DEPRECATED", "RETIRADO", "LEGADO NO VALIDADO")):
            status,reason="DEPRECATED_RETAINED","Self-declared deprecated/retired source; retain only with documented replacement."
        else:
            status,reason="ORPHAN_REVIEW","No production import or external entrypoint reference found."
        rows.append({
            "module":module,"path":path.name,"status":status,"reason":reason,
            "production_inbound":sorted(production_inbound[module]),
            "test_inbound":sorted(test_inbound[module]),
            "external_refs":sorted(external[module]),
        })
    return rows


def markdown(rows: list[dict]) -> str:
    lines=["# Porota — Module Lifecycle", "",
           "Generated by `tools/rc4_module_lifecycle.py`. Static evidence only; no module is deleted automatically.", "",
           "| Module | Status | Production inbound | External refs | Reason |",
           "|---|---|---:|---:|---|"]
    for row in rows:
        reason=str(row["reason"]).replace("|","/")
        lines.append(f"| `{row['module']}` | `{row['status']}` | {len(row['production_inbound'])} | {len(row['external_refs'])} | {reason} |")
    counts=defaultdict(int)
    for row in rows:
        counts[row["status"]]+=1
    lines.extend(["", "## Counts", ""]+[f"- `{key}`: {counts[key]}" for key in sorted(counts)])
    return "\n".join(lines)+"\n"


def main() -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument("root", nargs="?", default=".")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--fail-on-orphan", action="store_true")
    args=parser.parse_args()
    rows=analyze(Path(args.root))
    print(json.dumps(rows,ensure_ascii=False,indent=2) if args.json else markdown(rows), end="")
    unresolved=[r for r in rows if r["status"] in {"ORPHAN_REVIEW","RC4_INTEGRATION_PENDING","POLICY_LIBRARY_PENDING"}]
    return 3 if args.fail_on_orphan and unresolved else 0


if __name__ == "__main__":
    raise SystemExit(main())
