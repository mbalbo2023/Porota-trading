#!/usr/bin/env python3
"""RC4 module reachability audit.

Static imports alone are insufficient: Porota also starts Python modules through
subprocess, Docker and systemd/host scripts. This tool builds a conservative
reachability graph and requires every non-runtime module to be explicitly
classified before release.

It never imports project modules and never accesses network, broker or runtime
DBs. It only reads the source tree.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
from collections import defaultdict, deque
from pathlib import Path

ALLOWED_CLASSIFICATIONS = {
    "OFFLINE_TOOL", "MANUAL_ADMIN_TOOL", "TEST_ONLY",
    "DEPRECATED_WITH_REASON", "INTENTIONAL_LIBRARY", "PENDING_WIRING",
}
DEFAULT_ROOT_MODULES = {
    "entrypoint", "j_main", "o_dashboard",
    "bf_production_paper_observer", "bg_paper_dashboard",
}
TEXT_ROOT_PREFIXES = ("scripts/", "deploy/")
TEXT_ROOT_NAMES = {"Dockerfile", "docker-compose.yml"}


def module_name(root: Path, path: Path) -> str:
    parts=list(path.relative_to(root).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts=parts[:-1]
    return ".".join(parts)


def source_modules(root: Path) -> dict[str, Path]:
    return {module_name(root,p):p for p in root.rglob("*.py") if "__pycache__" not in p.parts}


def parse_imports(modules: dict[str,Path]) -> dict[str,set[str]]:
    known=set(modules)
    graph=defaultdict(set)
    for name,path in modules.items():
        try:
            tree=ast.parse(path.read_text(encoding="utf-8",errors="replace"),filename=str(path))
        except SyntaxError as exc:
            raise SystemExit(f"SYNTAX_ERROR={path}:{exc.lineno}:{exc.msg}") from exc
        raw=set()
        for node in ast.walk(tree):
            if isinstance(node,ast.Import):
                raw.update(alias.name for alias in node.names)
            elif isinstance(node,ast.ImportFrom) and node.module:
                raw.add(node.module)
            elif isinstance(node,ast.Call):
                # importlib.import_module("literal") / __import__("literal")
                func=node.func
                fname=(func.attr if isinstance(func,ast.Attribute) else
                       func.id if isinstance(func,ast.Name) else "")
                if fname in {"import_module","__import__"} and node.args:
                    arg=node.args[0]
                    if isinstance(arg,ast.Constant) and isinstance(arg.value,str):
                        raw.add(arg.value)
                # subprocess [sys.executable, "module.py", ...]
                for arg in node.args:
                    if isinstance(arg,(ast.List,ast.Tuple)):
                        for item in arg.elts:
                            if isinstance(item,ast.Constant) and isinstance(item.value,str) and item.value.endswith(".py"):
                                target=Path(item.value).stem
                                if target in known:
                                    graph[name].add(target)
        for imported in raw:
            if imported in known:
                graph[name].add(imported)
                continue
            # Longest known prefix, for package.submodule imports.
            candidates=[candidate for candidate in known if imported.startswith(candidate+".")]
            if candidates:
                graph[name].add(max(candidates,key=len))
    return graph


def text_entrypoints(root: Path, modules: dict[str,Path]) -> set[str]:
    roots=set()
    filenames={path.name:name for name,path in modules.items()}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel=path.relative_to(root).as_posix()
        if not (rel.startswith(TEXT_ROOT_PREFIXES) or path.name in TEXT_ROOT_NAMES or path.suffix in {".service",".timer",".sh"}):
            continue
        try:
            text=path.read_text(encoding="utf-8",errors="ignore")
        except OSError:
            continue
        for filename,name in filenames.items():
            if filename in text:
                roots.add(name)
    return roots


def reachable(graph: dict[str,set[str]], roots: set[str]) -> set[str]:
    seen=set(); queue=deque(sorted(roots))
    while queue:
        item=queue.popleft()
        if item in seen:
            continue
        seen.add(item)
        queue.extend(sorted(graph.get(item,())))
    return seen


def load_classification(path: Path | None) -> dict[str,dict]:
    if path is None or not path.exists():
        return {}
    payload=json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload,dict):
        raise SystemExit("CLASSIFICATION_MUST_BE_OBJECT")
    result={}
    for key,value in payload.items():
        if isinstance(value,str):
            value={"classification":value,"reason":""}
        if not isinstance(value,dict):
            raise SystemExit(f"INVALID_CLASSIFICATION={key}")
        result[str(key)]=value
    return result


def audit(root: Path, classification_path: Path | None=None) -> dict:
    root=root.resolve()
    modules=source_modules(root)
    graph=parse_imports(modules)
    roots={name for name in DEFAULT_ROOT_MODULES if name in modules}
    roots |= text_entrypoints(root,modules)
    live=reachable(graph,roots)
    classification=load_classification(classification_path)

    rows=[]; errors=[]
    for name,path in sorted(modules.items()):
        rel=path.relative_to(root).as_posix()
        if name in live:
            cls="RUNTIME_REACHABLE"
            reason="reachable from runtime/scheduled entrypoint"
        elif rel.startswith("tests/") or path.name.startswith("test_"):
            cls="TEST_ONLY"
            reason="test module"
        else:
            item=classification.get(name,{})
            cls=str(item.get("classification") or "ORPHAN_ERROR")
            reason=str(item.get("reason") or "")
            if cls not in ALLOWED_CLASSIFICATIONS:
                errors.append(f"{name}:UNCLASSIFIED")
            if cls in {"DEPRECATED_WITH_REASON","OFFLINE_TOOL","MANUAL_ADMIN_TOOL","INTENTIONAL_LIBRARY","PENDING_WIRING"} and not reason.strip():
                errors.append(f"{name}:REASON_REQUIRED")
        rows.append({"module":name,"path":rel,"classification":cls,"reason":reason})

    # Stale classification entries are also errors: they hide deleted/renamed modules.
    for name in sorted(set(classification)-set(modules)):
        errors.append(f"{name}:CLASSIFICATION_WITHOUT_MODULE")

    counts=defaultdict(int)
    for row in rows:
        counts[row["classification"]]+=1
    return {
        "schema_version":1,
        "root":str(root),
        "entrypoints":sorted(roots),
        "counts":dict(sorted(counts.items())),
        "errors":errors,
        "release_gate":"PASS" if not errors else "BLOCKED",
        "modules":rows,
    }


def main(argv=None) -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument("--root",default=".")
    parser.add_argument("--classification",default="RC4_MODULE_CLASSIFICATION.json")
    parser.add_argument("--output",default="")
    args=parser.parse_args(argv)
    root=Path(args.root)
    classification=(root/args.classification if args.classification else None)
    report=audit(root,classification)
    text=json.dumps(report,ensure_ascii=False,indent=2)+"\n"
    if args.output:
        Path(args.output).write_text(text,encoding="utf-8")
    print(text,end="")
    return 0 if report["release_gate"]=="PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
