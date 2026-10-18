"""Explicit test-only native compilation; production never compiles/falls back."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import sysconfig


def native_storage(folder):
    from rc6_shadow_runtime import persistence as p
    if p._native_json_bound is not None:
        assert callable(getattr(p._native_json_bound, 'snapshot_encode', None))
        return p._native_json_bound
    binary = Path(folder) / '_rc6_json_bound.so'
    source = Path(p.__file__).with_name('_rc6_json_bound.c')
    subprocess.run(['cc', '-O3', '-DNDEBUG', '-nostdlib', '-Wall', '-Wextra',
                    '-shared', '-fPIC', '-I'+sysconfig.get_path('include'), str(source),
                    '-o', str(binary)], check=True, timeout=60)
    spec = importlib.util.spec_from_file_location('_rc6_json_bound', binary)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def native_command(command, module):
    """Load the exact tested codec before persistence, in the measured child."""
    bootstrap = """import importlib.util,runpy,sys
binary=sys.argv.pop(1)
spec=importlib.util.spec_from_file_location('rc6_shadow_runtime._rc6_json_bound',binary)
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
sys.modules[spec.name]=module
sys.argv=sys.argv[1:]
runpy.run_path(sys.argv[0],run_name='__main__')
"""
    assert command[0] == sys.executable
    return [sys.executable, '-c', bootstrap, module.__file__, *command[1:]]
