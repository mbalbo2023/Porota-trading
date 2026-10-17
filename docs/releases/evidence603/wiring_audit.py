"""Reproduce static wiring reconciliation without activating any module.

Imports/literals show possible reachability, not a live execution trace.
Use an exact candidate checkout and its certified bundle/frozen manifests.
"""
import argparse
import ast
import collections
import hashlib
import json
from pathlib import Path
import re
import subprocess


def reconcile(repo, bundle, frozen):
    tree = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD^{tree}'], text=True).strip()
    if tree != frozen['candidate_tree_sha']:
        raise ValueError('AUDIT_TREE_DOES_NOT_MATCH_FROZEN_CANDIDATE')
    paths = subprocess.check_output(['git', '-C', str(repo), 'ls-files'], text=True).splitlines()
    py = [p for p in paths if p.endswith('.py')]
    roots = [p for p in py if '/' not in p]
    modules = {p: '.'.join(Path(p).with_suffix('').parts).removesuffix('.__init__') for p in py}
    by_module = {value: key for key, value in modules.items()}
    by_name = {Path(p).name: p for p in roots}
    shells = [p for p in paths if p.endswith('.sh')]
    edges = collections.defaultdict(set)
    callers = collections.defaultdict(list)
    errors = []

    def edge(source, dest, line, kind):
        edges[source].add(dest)
        value = {'path': source, 'line': line, 'kind': kind}
        if value not in callers[dest]:
            callers[dest].append(value)

    for path in py:
        if path.startswith('tests/') or Path(path).name.startswith('test_'):
            continue
        try:
            parsed = ast.parse((repo / path).read_text())
        except Exception as error:
            errors.append({'path': path, 'error': type(error).__name__})
            continue
        package = modules[path].split('.')
        if Path(path).name != '__init__.py':
            package = package[:-1]
        for node in ast.walk(parsed):
            targets = []
            if isinstance(node, ast.Import):
                targets = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                prefix = package[:len(package) - node.level + 1] if node.level else []
                stem = '.'.join(prefix + ([node.module] if node.module else []))
                targets = [stem] + [stem + '.' + a.name for a in node.names]
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                name = Path(node.value).name
                if name in by_name and node.value.endswith('.py'):
                    edge(path, by_name[name], node.lineno, 'POSSIBLE_FILENAME_LITERAL')
            for target in targets:
                parts = target.split('.')
                for length in range(1, len(parts) + 1):
                    dest = by_module.get('.'.join(parts[:length]))
                    if dest:
                        edge(path, dest, node.lineno, 'AST_IMPORT')

    for path in shells:
        for line, text in enumerate((repo / path).read_text().splitlines(), 1):
            for name, dest in by_name.items():
                if name in text:
                    edge(path, dest, line, 'POSSIBLE_SHELL_FILENAME_LITERAL')

    policy = json.loads((repo / 'ops/policy/host-control-plane-reconciliation-v2.json').read_text())
    unit_refs = collections.defaultdict(list)
    unit_shell_roots = set()
    for path, row in policy['units'].items():
        if row['role'] != 'ACTIVE_SERVICE' or not row.get('install'):
            continue
        # systemd continuation lines belong to the same ExecStart directive.
        text = (repo / path).read_text().replace('\\\n', ' ')
        starts = '\n'.join(s for s in text.splitlines() if s.startswith('ExecStart='))
        for name, dest in by_name.items():
            if name in starts:
                unit_refs[dest].append(path)
        for shell in shells:
            if shell in starts or Path(shell).name in starts:
                unit_shell_roots.add(shell)

    observed_entrypoints = ['bv_paper_runtime.py', 'o_dashboard.py']
    reachable = set()
    queue = list(set(observed_entrypoints) | set(unit_refs) | unit_shell_roots)
    while queue:
        path = queue.pop()
        if path in reachable:
            continue
        reachable.add(path)
        queue.extend(edges[path] - reachable)

    workflow = (repo / '.github/workflows/porota-deploy-v2-promote.yml').read_text()
    workflows = [(p, (repo / p).read_text()) for p in paths
                 if p.startswith('.github/workflows/') and p.endswith(('.yml', '.yaml'))]
    tests = [(p, (repo / p).read_text()) for p in py
             if p.startswith('tests/') or Path(p).name.startswith('test_')]
    hashes = {row['path']: row['sha256'] for row in bundle['files']}
    rows = []
    for path in sorted(roots):
        pattern = re.compile(r'(?<![A-Za-z0-9_])' + re.escape(Path(path).stem) + r'(?![A-Za-z0-9_])')
        workflow_refs = [name for name, text in workflows if pattern.search(text)]
        test_refs = [name for name, text in tests if pattern.search(text)]
        if path.startswith('test_'):
            kind = 'TEST_MODULE_PRESERVED'
        elif path in observed_entrypoints:
            kind = 'CONTAINER_ENTRYPOINT_OBSERVED_AT_START'
        elif path in unit_refs:
            kind = 'VERSIONED_ACTIVE_SERVICE_REFERENCE'
        elif path in reachable:
            kind = 'STATIC_POSSIBLE_REACHABILITY_FROM_ACTIVE_ROOTS'
        elif pattern.search(workflow):
            kind = 'CANONICAL_DEPLOY_CONTROL_PLANE_REFERENCE'
        elif workflow_refs:
            kind = 'WORKFLOW_REFERENCE_ONLY_NOT_RUNTIME_PROOF'
        elif test_refs:
            kind = 'TEST_REFERENCE_ONLY_LIVE_AUTHORITY_UNKNOWN'
        else:
            kind = 'PRESERVED_LIVE_AUTHORITY_UNKNOWN'
        digest = hashlib.sha256((repo / path).read_bytes()).hexdigest()
        if path in hashes and hashes[path] != digest:
            raise ValueError('AUDIT_CERTIFIED_BUNDLE_HASH_MISMATCH:' + path)
        rows.append({'path': path, 'classification': kind, 'file_sha256': digest,
                     'exact_certified_host_bundle_hash': path in hashes,
                     'active_unit_references': unit_refs[path],
                     'possible_caller_examples': callers[path][:2],
                     'possible_caller_count': len(callers[path]),
                     'workflow_reference_count': len(workflow_refs), 'workflow_examples': workflow_refs[:2],
                     'test_reference_count': len(test_refs), 'test_examples': test_refs[:2],
                     'runtime_authority': 'OBSERVED_AT_CONTAINER_START' if path in observed_entrypoints
                     else 'NOT_PROVEN_BY_STATIC_ANALYSIS', 'code_preserved': True})
    return {'schema': 'rc6.wiring-reconciliation.v1', 'candidate_sha': frozen['candidate_sha'],
            'candidate_tree_sha': tree, 'method': __doc__, 'root_python_count': len(rows),
            'classification_counts': dict(collections.Counter(row['classification'] for row in rows)),
            'certified_host_bundle_root_count': sum(row['exact_certified_host_bundle_hash'] for row in rows),
            'observed_entrypoints': observed_entrypoints,
            'active_service_roots': {path: refs for path, refs in unit_refs.items() if refs},
            'active_service_shell_roots': sorted(unit_shell_roots), 'parse_errors': errors, 'rows': rows}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    for name in ('repo', 'bundle', 'frozen', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    result = reconcile(args.repo, json.loads(args.bundle.read_text()), json.loads(args.frozen.read_text()))
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n')
    print(json.dumps({key: result[key] for key in
                      ('root_python_count', 'classification_counts', 'certified_host_bundle_root_count', 'parse_errors')}))
