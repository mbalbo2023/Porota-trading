#!/usr/bin/env python3
"""Bounded, read-only RC6 storage and Python wiring inventory.

No table, index, module or historical revision is removed by this audit.
An unreferenced module is a review candidate, never proof of a dead caller:
Actions, systemd, shell, dynamic imports and external users also matter.
"""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
import sqlite3
from urllib.parse import quote


MAX_SAMPLE = 1000
MAX_OPCODES = 200_000
ROOTS = (".github/workflows", "ops", "scripts", "systemd", "Dockerfile", "docker-compose.yml")


def _columns(connection, name):
    # name comes only from sqlite_master, never external input.
    return {row[1] for row in connection.execute('PRAGMA table_info("'+name.replace('"','""')+'")')}


def inspect_database(path: Path, sample: int = 250) -> dict:
    """No full-table scans; bounded recent-row sample is diagnostic only."""
    if not 1 <= sample <= MAX_SAMPLE:
        raise ValueError("SAMPLE_BUDGET_INVALID")
    result={"path":str(path),"state":"NO_VERIFICADO","read_only":True,
            "sample_limit":sample,"deletion_allowed":False}
    if not path.is_file():
        return dict(result,reason="DATABASE_NOT_AVAILABLE")
    result["file_bytes"]=path.stat().st_size
    wal=Path(str(path)+"-wal")
    result["wal_bytes_at_read"]=wal.stat().st_size if wal.is_file() else 0
    uri="file:"+quote(str(path.resolve()),safe="/")+"?mode=ro"
    with sqlite3.connect(uri,uri=True,timeout=0.2) as connection:
        connection.execute("PRAGMA query_only=ON")
        connection.set_progress_handler(lambda: 1,MAX_OPCODES)
        page_size=connection.execute("PRAGMA page_size").fetchone()[0]
        page_count=connection.execute("PRAGMA page_count").fetchone()[0]
        free_count=connection.execute("PRAGMA freelist_count").fetchone()[0]
        result.update(page_size=page_size,page_count=page_count,
                      freelist_pages=free_count,freelist_bytes_estimate=free_count*page_size)
        names={row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        result["table_count"]=len(names)
        result["index_count"]=connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='index'").fetchone()[0]
        if "history_versions_v2" in names:
            cols=_columns(connection,"history_versions_v2")
            required={"symbol","instrument_type","market","currency","settlement",
                      "date","price_basis","adjustment_basis","source","payload_hash",
                      "previous_version_id","version_known_at"}
            if not required <= cols:
                result["history"]={"state":"LEGACY_IDENTITY_INCOMPLETE",
                                   "missing_columns":sorted(required-cols)}
            else:
                # This is not a duplicate detector for the whole DB. ABA and
                # provider revisions may legitimately repeat the same payload.
                rows=connection.execute("""SELECT symbol,instrument_type,market,currency,
                    settlement,date,price_basis,adjustment_basis,source,payload_hash,
                    previous_version_id FROM history_versions_v2
                    ORDER BY id DESC LIMIT ?""",(sample,)).fetchall()
                keys=[tuple(row) for row in rows]
                result["history"]={"state":"SAMPLED_ONLY","sampled_rows":len(rows),
                    "same_exact_revision_key_in_sample":len(keys)-len(set(keys)),
                    "whole_database_duplicates":"NO_VERIFICADO",
                    "preserve_aba_revisions":True}
    return result


def inspect_wiring(root: Path) -> dict:
    """Count static Python imports and textual entrypoints; never auto-delete."""
    files=sorted(p for p in root.rglob("*.py") if ".git" not in p.parts)
    local={p.stem for p in files if p.parent==root}
    references=set()
    syntax_errors=[]
    for path in files:
        try:
            tree=ast.parse(path.read_text(encoding="utf-8"),filename=str(path))
        except (SyntaxError,UnicodeError):
            syntax_errors.append(str(path.relative_to(root)))
            continue
        for node in ast.walk(tree):
            if isinstance(node,ast.Import):
                references.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node,ast.ImportFrom) and node.module:
                references.add(node.module.split(".")[0])
    # A .py string in workflow/ops/shell configuration is an entrypoint clue.
    # Keep it conservative: no claim that static absence proves non-use.
    external=[]
    for name in ROOTS:
        item=root/name
        if item.is_file(): external.append(item)
        elif item.is_dir(): external.extend(p for p in item.rglob("*") if p.is_file() and p.suffix in {".yml",".yaml",".sh",".service",".timer",".json"})
    for path in external:
        try: text=path.read_text(encoding="utf-8")
        except (UnicodeError,OSError): continue
        references.update(stem for stem in local if stem+".py" in text)
    candidates=sorted(local-references)
    return {"python_files":len(files),"root_modules":len(local),
            "static_or_config_referenced_root_modules":len(local & references),
            "unreferenced_review_candidates":candidates,
            "syntax_errors":syntax_errors,"deletion_allowed":False,
            "classification":"STATIC_REVIEW_ONLY_DYNAMIC_CALLERS_NOT_EXCLUDED"}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository",type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument("--database",action="append",type=Path,default=[])
    parser.add_argument("--sample",type=int,default=250)
    parser.add_argument("--database-only",action="store_true",
                        help="Skip source scan for a bounded host database probe")
    args=parser.parse_args()
    report={"schema":"RC6_STORAGE_WIRING_READONLY_V1",
            "wiring":None if args.database_only else inspect_wiring(args.repository),
            "databases":[inspect_database(path,args.sample) for path in args.database]}
    print(json.dumps(report,sort_keys=True,separators=(",",":")))


if __name__=="__main__":
    main()
