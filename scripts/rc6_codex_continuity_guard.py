#!/usr/bin/env python3
"""Static, same-checkout continuity guard for the RC6 Predeploy V2 candidate.

Live owner/runtime checks remain the authority of GitHub and Deploy V2.
"""
from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

FILES = ("AGENTS.md", "README.md", "docs/codex/RC6_AGENT_CONTINUITY_V1.md",
         "ops/policy/porota-policy.yaml")
REQUIRED = {
    "AGENTS.md": ("PAPER/SHADOW", "PPI Watch", "FIX-FORWARD", "docs/codex/RC6_AGENT_CONTINUITY_V1.md"),
    "README.md": ("PRODUCTION_PAPER", "real_orders_sent=0", "PPI Watch", "FIX-FORWARD",
                  "docs/codex/RC6_AGENT_CONTINUITY_V1.md", "main", "NO_VERIFICADO"),
    "docs/codex/RC6_AGENT_CONTINUITY_V1.md": ("PAPER/SHADOW", "PPI Watch", "FIX-FORWARD",
          "WRITE_OWNER", "DEPLOY_OWNER", "NO_VERIFICADO", "main", "Droplet"),
    "ops/policy/porota-policy.yaml": ("required_mode: PRODUCTION_PAPER",
          "real_trading_allowed: false", "rollback_allowed: false", "backups_suspended_by_operator: true"),
}


def check(root: Path) -> list[str]:
    failures = []
    texts = {}
    for relative in FILES:
        path = root / relative
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 512 * 1024:
            failures.append("MISSING_OR_UNSAFE:" + relative)
            continue
        try:
            texts[relative] = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            failures.append("UNREADABLE:" + relative)
    for relative, tokens in REQUIRED.items():
        text = texts.get(relative, "")
        failures.extend("CONTRACT_MISSING:" + relative + ":" + token
                        for token in tokens if token not in text)
    readme = texts.get("README.md", "").lower()
    for legacy in ("simulación contra el sandbox de ppi, real",
                   "responder 🧪 simulación", "docker compose up -d"):
        if legacy in readme:
            failures.append("LEGACY_REAL_OPERATOR_INSTRUCTIONS:" + legacy)
    if "main no es evidencia de lo instalado" not in texts.get("docs/codex/RC6_AGENT_CONTINUITY_V1.md", ""):
        failures.append("MAIN_RUNTIME_CONFUSION")
    return failures


def self_test(root: Path) -> None:
    source = {name: (root / name).read_text(encoding="utf-8") for name in FILES}
    with tempfile.TemporaryDirectory(prefix="rc6-continuity-") as tmp:
        base = Path(tmp)
        for name, content in source.items():
            path = base / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        assert not check(base), "valid candidate rejected"
        doc = base / FILES[2]
        doc.unlink()
        assert check(base), "missing continuity contract accepted"
        doc.write_text(source[FILES[2]].replace("main no es evidencia de lo instalado",
                                                  "main prueba el runtime"), encoding="utf-8")
        assert "MAIN_RUNTIME_CONFUSION" in check(base)
        doc.write_text(source[FILES[2]], encoding="utf-8")
        readme = base / "README.md"
        readme.write_text(source["README.md"] + "\ndocker compose up -d\n", encoding="utf-8")
        assert any(x.startswith("LEGACY_REAL_OPERATOR_INSTRUCTIONS") for x in check(base))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    failures = check(root)
    if args.self_test and not failures:
        self_test(root)
    print("RC6_CODEX_CONTINUITY=" + ("RED:" + ",".join(failures) if failures else "GREEN"))
    return 2 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
