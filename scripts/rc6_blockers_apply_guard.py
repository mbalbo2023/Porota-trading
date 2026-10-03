"""Exact, bounded source patcher. Used only on the isolated fix branch."""
import ast
from pathlib import Path
import subprocess
import sys

root=Path(sys.argv[1]).resolve()
path=root/'bf_production_paper_observer.py'
original=path.read_text()
blob=subprocess.check_output(['git','hash-object',str(path)],cwd=root,text=True).strip()
if blob!='51e98d744907b5f894f8f0b80515b99ac3dcb609':
    raise SystemExit('RECONCILER_BASE_BLOB_DRIFT')
parsed=ast.parse(original)
fn=next(n for n in parsed.body if isinstance(n,ast.FunctionDef) and n.name=='_reconcile_complementary_catalog')
lines=original.splitlines(keepends=True)
section=''.join(lines[fn.lineno-1:fn.end_lineno])
old='''            primary_capability = str(primary.get("capability") or "")
            if primary_capability in {
                "TYPE_NOT_ENUMERATED", "MARKET_NOT_ENUMERATED", "UNSUPPORTED_FAMILY",
                "CONTRACT_EVIDENCE_REVIEW_REQUIRED", "CONTRACT_SOURCE_CONFLICT",
            }:
                continue
'''
new='''            # A local review may be reconsidered only with complete, exact
            # canonical V2 evidence. Genuine PPI vetoes and material conflicts
            # cannot be cleared by this reconciliation path.
            if not reconciliation_allowed(primary, raw, checked_at=checked):
                continue
'''
if section.count(old)!=1:raise SystemExit('REVIEW_GUARD_ANCHOR_MISMATCH')
section=section.replace(old,new)
anchor='    market_root = Path(os.getenv("POROTA_MARKET_DATA_ROOT", "/app/data/market"))\n'
if section.count(anchor)!=1:raise SystemExit('FUNCTION_IMPORT_ANCHOR_MISMATCH')
section=section.replace(anchor,'    from rc6_contract_reconciliation_guard import (\n        reconciliation_allowed, select_verified_primary)\n\n'+anchor)
anchor='''            if len(matches) > 1:
                family = financial_catalog.canonical_family('''
replacement='''            # A stale alias does not overrule a unique current PPI identity
            # when ISIN, quote basis and all other dimensions prove equality.
            matches = select_verified_primary(matches, raw, checked_at=checked)
            if len(matches) > 1:
                family = financial_catalog.canonical_family('''
if section.count(anchor)!=1:raise SystemExit('ALIAS_SELECTION_ANCHOR_MISMATCH')
section=section.replace(anchor,replacement)
updated=''.join(lines[:fn.lineno-1])+section+''.join(lines[fn.end_lineno:])
after=ast.parse(updated)
def without_target(tree):
    return [ast.dump(n,include_attributes=False) for n in tree.body
            if not (isinstance(n,ast.FunctionDef) and n.name=='_reconcile_complementary_catalog')]
if without_target(parsed)!=without_target(after):raise SystemExit('OUT_OF_SCOPE_AST_CHANGE')
path.write_text(updated)
print('RECONCILER_PATCH_SCOPE=GREEN')
