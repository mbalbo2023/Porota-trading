"""Closed Source inventory of G5A process and write destinations.

The inventory is a guard input, not a replacement for the kernel boundary.
An unknown or unconfined call site is a NO-GO before a material launch.
"""
from __future__ import annotations

import ast
import hashlib
from pathlib import Path

SCHEMA = 'porota.rc6.g5a-writer-inventory.v1'
MEMBERS = (
    'scripts/rc6_g5a_actions.py', 'scripts/rc6_g5b_native_readers.py',
    'scripts/rc6_material_carrier.py', 'scripts/rc6_material_horizon.py',
    'scripts/rc6_archive_reader_review.py', 'scripts/rc6_controlled_governed_runner.py',
    'scripts/rc6_capacity_calibration.py', 'scripts/rc6_g5a_carrier.py',
    'scripts/rc6_material_focal.py', 'scripts/rc6_authenticated_fixture_lifecycle.py',
)
ENVIRONMENT_WRITERS = frozenset(('HOME', 'TMPDIR', 'TMP', 'TEMP', 'XDG_CACHE_HOME',
    'XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_RUNTIME_DIR', 'PIP_CACHE_DIR',
    'PIP_BUILD_TRACKER', 'PIP_SRC', 'CARGO_HOME', 'RUSTUP_HOME', 'CCACHE_DIR',
    'PYTHONPYCACHEPREFIX'))
STAGES = ('preparation', 'reader-collection', 'reader-execution')


def _calls(member, raw):
    tree = ast.parse(raw, filename=member)
    rows = []

    class Visitor(ast.NodeVisitor):
        def __init__(self):
            self.scope = []

        def visit_FunctionDef(self, node):
            self.scope.append(node.name)
            self.generic_visit(node)
            self.scope.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Call(self, node):
            name = ast.unparse(node.func)
            if (name.startswith('subprocess.') or name in ('os.system', 'os.popen',
                    'horizon.preflight', 'original.complete_full_git', 'original.fetch_originals',
                    'original.git', 'governed.source_pin', 'readers.inventory_from_source_pin',
                    'privileged.service_command', 'lifecycle.execute_owned', 'owned.create_namespace',
                    'tempfile.mkdtemp', 'subprocess.Popen', 'subprocess.run')):
                rows.append({'member': member, 'function': '.'.join(self.scope),
                             'line': node.lineno, 'call': name,
                             'managed_wire': name == 'horizon.preflight' and
                                 any(key.arg == 'wire' for key in node.keywords)})
            self.generic_visit(node)

    Visitor().visit(tree)
    return rows


def audit(source_root, *, project_root=None, destinations=None):
    root = Path(source_root).absolute()
    blobs = {member: (root / member).read_bytes() for member in MEMBERS}
    calls = sorted((row for member, raw in blobs.items() for row in _calls(member, raw)),
                   key=lambda row: (row['member'], row['line'], row['call']))
    # Source is the input to the native child. It must itself reside in the
    # project before Git/Source can be regarded as covered by the host quota.
    outside = []
    if project_root is None:
        outside.append('PROJECT_ROOT_NOT_ESTABLISHED')
    else:
        project = Path(project_root).absolute()
        if root != project and not root.is_relative_to(project):
            outside.append('SOURCE_REPOSITORY_OUTSIDE_PROJECT')
        if destinations is None or set(destinations) != ENVIRONMENT_WRITERS:
            outside.append('ALL_ENVIRONMENT_WRITE_DESTINATIONS_REQUIRED')
        else:
            for name, value in destinations.items():
                path = Path(value).absolute()
                if path != project and not path.is_relative_to(project):
                    outside.append('WRITE_DESTINATION_OUTSIDE_PROJECT:' + name)
    # These sites currently start processes in the NONROOT parent. An original
    # manager child wrapper does not cover their output or a Git child of one.
    unconfined = [row for row in calls if
        (row['member'] == 'scripts/rc6_g5b_native_readers.py' and
         (row['call'] in ('governed.source_pin', 'readers.inventory_from_source_pin')
          or row['call'] == 'horizon.preflight' and not row['managed_wire'])) or
        (row['member'] == 'scripts/rc6_g5a_actions.py' and row['call'] == 'original.git') or
        (row['member'] == 'scripts/rc6_material_carrier.py' and
         row['call'] in ('subprocess.run',))]
    # The initial Actions checkout/setup-python and pip wheel installation
    # precede the existing stage setup. They are process writers too.
    workflow = (root / '.github/workflows/rc6-unified-candidate-tests.yml').read_text()
    external_steps = [name for name in ('actions/checkout@', 'actions/setup-python@') if name in workflow]
    if external_steps:
        outside.append('PRE_QUOTA_ACTIONS_WRITERS')
    return {'schema': SCHEMA, 'source_members_sha256':
            {member: hashlib.sha256(raw).hexdigest() for member, raw in blobs.items()},
            'process_call_sites': calls, 'unconfined_parent_call_sites': unconfined,
            'write_destinations': {key: str(value) for key, value in (destinations or {}).items()},
            'outside_project': outside, 'pre_quota_workflow_steps': external_steps,
            'stages': list(STAGES), 'all_writers_enclosed': not outside and not unconfined,
            'native_enforcement_proved': False, 'G5_GREEN': False}


def require_closed(inventory):
    if type(inventory) is not dict or inventory.get('schema') != SCHEMA:
        raise ValueError('G5A_WRITER_INVENTORY_SOURCE_REQUIRED')
    if inventory['outside_project']:
        raise ValueError(inventory['outside_project'][0])
    if inventory['unconfined_parent_call_sites']:
        raise ValueError('G5A_UNCONFINED_PARENT_PROCESS_PRESENT')
    if not inventory['all_writers_enclosed']:
        raise ValueError('G5A_ALL_WRITERS_NOT_ENCLOSED')
    return inventory
