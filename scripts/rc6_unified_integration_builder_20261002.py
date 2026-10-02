"""One-shot source integration on a pre-created isolated branch; never deploys."""
from pathlib import Path
import ast
import json
import os
import runpy
import shutil
import subprocess
import sys

BASE = '136453dc5b6d4146057142bcdd76b2f6b8d474e0'
SEED = '9a45dd700333924e312f7d412c148692db96de4b'
TARGET = 'integrate/rc6-unified-20261002'
PRODUCT = 'deploy/rc6-pr69-isolated-20260915'
INPUTS = {
    435: ('2c55af7ab4407b9fae218a96f06300a7e0670c71', 'fix/rc6-deploy-image-retention-guard-20261001'),
    436: ('fd84b4dd736e9f4ebd011495ab16df282ac51d37', 'fix/rc6-gdelt-retired-20261002'),
    437: ('5ce5927525866ad001348d48a2eb19850557a3c7', 'fix/rc6-scalping-confirmation-capacity-20261002'),
    438: ('217dbadfe418277f10ee0304c2a9ae0e02d723b9', 'fix/rc6-contract-material-hash-20261002'),
    439: ('fe34c2e87d7bb53503cca173209e814b93b45e6e', 'fix/rc6-dashboard-truthful-explanations-20261002'),
    440: ('2c2969e8accf7009ee338ae109258b80d042cb97', 'fix/rc6-exit-reader-sqlite-lock-20261002'),
    442: ('e67612fbba198d6c51ac588b38a56bbb57b73314', 'fix/rc6-bcra-retired-20261002'),
    443: ('67a7e06561337b2aaeff76dcf3ede15af013f764', 'fix/rc6-dashboard-history-tablet-truth-20261002'),
    444: ('9dde0dfa224ffa9301458b61a5fc659524d831ea', 'fix/rc6-caucion-policy-wiring-20261002'),
}
HERE = Path(__file__).resolve().parent
OUT = Path('/tmp/rc6-integration-evidence')
OUT.mkdir(exist_ok=True)


def git(*args):
    return subprocess.check_output(['git', *args], text=True).strip()


def verify_heads():
    expected = {PRODUCT: BASE, TARGET: SEED}
    expected.update({branch: sha for sha, branch in INPUTS.values()})
    remote = git('ls-remote', 'origin', *['refs/heads/' + branch for branch in expected])
    actual = {line.split()[1].removeprefix('refs/heads/'): line.split()[0]
              for line in remote.splitlines()}
    assert actual == expected, {'reason': 'HEAD_DRIFT', 'expected': expected, 'actual': actual}
    print('RC6_FROZEN_HEADS=GREEN', flush=True)


verify_heads()
assert not git('status', '--porcelain'), 'audit checkout must be clean'
git('checkout', '--detach', SEED)
assert git('rev-parse', 'HEAD^') == BASE
assert git('rev-parse', 'HEAD:.github/workflows/porota-deploy-v2-promote.yml') == '2b5ae4db11d466f6b12375ec5cff2ed30312eddc'
conflicts = ('tests/test_rc6_dashboard_truthful_explanations.py', 'tests/test_rc6_macro_risk_shadow.py')
excluded = []
for number, (sha, branch) in INPUTS.items():
    assert git('merge-base', BASE, sha) == BASE, f'input {number} diverged from declared base'
    excluded.extend({'pr': number, 'path': path} for path in git('diff', '--name-only', BASE, sha).splitlines()
                    if path.startswith('.github/') and number != 435)
    paths = ['.', ':(exclude).github/**']
    if number == 443:
        paths += [':(exclude)' + path for path in conflicts]
    patch = subprocess.check_output(['git', 'diff', '--binary', BASE, sha, '--', *paths])
    if patch:
        subprocess.run(['git', 'apply', '--3way', '--index', '-'], input=patch, check=True)
    print(f'RC6_INPUT_APPLIED={number}|{sha}', flush=True)

# Preserve the union of dashboard explanations, BCRA retirement and visual tests.
truthful = git('show', INPUTS[439][0] + ':' + conflicts[0]) + '\n\n'
truthful += git('show', INPUTS[443][0] + ':' + conflicts[0]) + '\n'
Path(conflicts[0]).write_text(truthful)
macro = git('show', INPUTS[442][0] + ':' + conflicts[1]) + '\n\n'
other = git('show', INPUTS[443][0] + ':' + conflicts[1])
function = next(node for node in ast.parse(other).body if isinstance(node, ast.FunctionDef)
                and node.name == 'test_trading_dashboard_does_not_expose_retired_bcra_shadow_as_active_surface')
macro += ast.get_source_segment(other, function) + '\n'
Path(conflicts[1]).write_text(macro)

runpy.run_path(str(HERE / 'rc6_unified_integration_fixes_20261002.py'))
shutil.copyfile(HERE / 'rc6_unified_regression_fixture_20261002.py', 'tests/test_rc6_unified_20261002.py')
manifest = {
    'workstream': 'WS-INTEG-RC6-20261002', 'base_sha': BASE, 'seed_sha': SEED,
    'candidate_branch': TARGET, 'inputs': {str(n): {'sha': sha, 'branch': branch} for n, (sha, branch) in INPUTS.items()},
    'excluded_oneoff_workflows': excluded,
    'resolved_overlaps': ['rc6_contract_bridge.py: disjoint hunks', *conflicts],
    'paper_only': True, 'ppi_watch': 'UNTOUCHED', 'deploy': 'NOT_EXECUTED',
    'cross_guards': ['live caucion material contract recheck', 'exact live PPI source',
                     'live-book PAPER tariff accepted by sweep', 'dynamic freshness preserved',
                     'synthetic ledger placement and durable recovery', 'GDELT active cards/engine retired',
                     'nonzero MAE prefix preserved', 'STOP normalization idempotent'],
}
Path('docs/releases').mkdir(parents=True, exist_ok=True)
Path('docs/releases/RC6_UNIFIED_INPUTS_2026-10-02.json').write_text(json.dumps(manifest, indent=2) + '\n')
Path('docs/releases/RC6_UNIFIED_HANDOFF_2026-10-02.md').write_text('''# RC6 unified candidate — 2026-10-02

WORKSTREAM_ID: WS-INTEG-RC6-20261002. Scope: isolated integration only.
Exact inputs and exclusions: RC6_UNIFIED_INPUTS_2026-10-02.json.
PAPER/SHADOW ONLY. PPI Watch UNTOUCHED. FIX-FORWARD ONLY.

## Reconciliation and permanent guards
- Nine frozen PR heads are integrated against the live product base. Historical PRs #426–#431 are already absorbed and are not reapplied.
- rc6_contract_bridge.py has disjoint source hunks. Conflicting dashboard test additions are combined; obsolete macro tests are replaced by retirement tests while retaining the dashboard-absence guard.
- RCA: GDELT tombstones left active dashboard cards and an engine call. Fix: no active cards or imports; static retirement marker only. Guard: cross-layer regression; historical data remains intact.
- RCA: caucion live-book reader removed the direct pending-change check. Fix: material-aware append-only evidence recheck even while catalog still says READY; strict exact PPI book source. Guard: material/provenance race and unknown-source regressions.
- RCA: the sweep fee filter accepted only the old notional policy, rejecting the new live-bid policy after successful book wiring. Fix: accept both explicitly authorized ARS PAPER policy names, preserving fee authority, depth participation and freshness. Guard: synthetic book-to-sweep-to-real-PAPER-ledger regression, with durable daily recovery.
- RCA: zero-MAE rendering could match the zero prefix of a nonzero value; repeated STOP normalization duplicated explanations. Fix: numeric boundary and idempotent translation. Guard: explicit nonzero and repeat-normalization cases.

## Release gates
Builder focused tests are preliminary evidence only. The exact final PR HEAD must pass canonical full Predeploy V2 and freeze its artifact before promotion. Deployment must use a two-parent merge preserving candidate tree identity. The builder cannot access the droplet and cannot deploy.
One-off input workflows and these integration tools stay on the audit branch; the existing canonical Deploy V2 guard from #435 is preserved.

## Runtime still pending
No new deployment or live operational validation is asserted here. Check immediate and delayed runtime, mode, zero real orders, PPI Watch, services, images, disk, dashboard, and retired sources. Caucion/scalping market-window behavior remains NO_VERIFICADO until valid fresh market evidence; do not fabricate fills, extend liquidity windows or relax safeguards to produce activity.
Ownership/evidence tracker: issue #441. DEPLOY_OWNER must be claimed separately after exact Predeploy GREEN and fresh concurrency verification.
''')

git('add', '-A')
changed = git('diff', '--cached', '--name-only', BASE).splitlines()
assert not any('ppi' in path.lower() and 'watch' in path.lower() for path in changed)
assert [p for p in changed if p.startswith('.github/')] == ['.github/workflows/porota-deploy-v2-promote.yml']
git('diff', '--cached', '--check')
subprocess.run([sys.executable, '-m', 'compileall', '-q', '-x', r'(^|/)(\.git|\.venv|venv|tests|docs)/', '.'], check=True)
focused = [path for path in changed if path.startswith('tests/test_') and path.endswith('.py')]
focused += ['tests/test_cash_sweep_runtime_hf6.py', 'tests/test_caucion_ledger_v17.py',
            'tests/test_caucion_treasury_v17.py', 'tests/test_ws_caucion_10_auto_recovery.py']
focused = sorted(set(focused))
(OUT / 'focused-test-paths.json').write_text(json.dumps(focused, indent=2) + '\n')
env = dict(os.environ, PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
subprocess.run([sys.executable, '-m', 'pytest', '-q', *focused,
                '--junitxml=' + str(OUT / 'focused-tests.xml')], env=env, check=True)
git('diff', '--exit-code')
verify_heads()
git('config', 'user.name', 'github-actions[bot]')
git('config', 'user.email', '41898282+github-actions[bot]@users.noreply.github.com')
git('commit', '-m', 'integrate(rc6): reconcile nine frozen PRs and enforce cross-workstream guards')
sha = git('rev-parse', 'HEAD')
tree = git('rev-parse', 'HEAD^{tree}')
subprocess.run(['git', 'push', 'origin', f'HEAD:refs/heads/{TARGET}'], check=True)
(OUT / 'integration-evidence.json').write_text(json.dumps({**manifest, 'candidate_sha': sha,
    'candidate_tree': tree, 'status': 'FOCUSED_GREEN_AND_EN_GITHUB',
    'full_predeploy': 'PENDING', 'runtime': 'NO_VERIFICADO'}, indent=2) + '\n')
(OUT / 'candidate.patch').write_bytes(subprocess.check_output(['git', 'diff', '--binary', BASE, 'HEAD']))
print(f'RC6_UNIFIED_CANDIDATE={sha}|tree={tree}|branch={TARGET}', flush=True)
