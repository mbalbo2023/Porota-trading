"""Declared environment bindings of immutable RC6 preparation drivers; no native overlays."""
import ast
import difflib
import hashlib
import os
from pathlib import Path

BASE = 'docs/audits/convergence/evidence/controlled-successor-20261006/checkpoint7-read-diagnostics/'
BUILDER = BASE+'full-gov-builder-prepared-only/build_governed_sources.py.source'
OUTER = BASE+'full-gov-prepared-only/governed_outer.py.source'
OBJECTS = BASE+'full-gov-prepared-only/original-required-19-objects.json'
PINS = {
    BUILDER:'03d08c3f362b12da70defa8f1e2388373d41e20a4ffc08e46f48fb9691667738',
    OUTER:'7fa1aae01b863695e43f1b4de0b862fb8757281ea5506d791107651c5d057bc4',
    OBJECTS:'492a3c0de260501776ee235ea5472da373d6c5881a4dfafca05518297fc16579',
}
G5P_CANDIDATE_BRANCH = 'fix/rc6-takeover-paper-sqlite-20261012'
G5P_CANDIDATE_PR = 497
G5P_BASE_BRANCH = 'deploy/rc6-pr69-isolated-20260915'
G5P_BASE_SHA = 'da697c6e6c2274579f9e4a112fabc4327475dd35'
G5P_BASE_TREE = '96a112a55ed779df3f7056b1e30d81aac8d0b791'


def expected_candidate_scope():
    return {'pr': G5P_CANDIDATE_PR, 'branch': G5P_CANDIDATE_BRANCH,
        'base_branch': G5P_BASE_BRANCH, 'base_sha': G5P_BASE_SHA,
        'base_tree': G5P_BASE_TREE,
        'authorization_schema': 'porota.rc6.candidate-launch.v1'}


def authority_literal_derivation(builder, candidate_scope):
    """Change only the two obsolete PR476 literals for the exact candidate PR."""
    if candidate_scope is None:
        return builder
    if type(candidate_scope) is not dict or candidate_scope != expected_candidate_scope():
        raise ValueError('G5P_BUILDER_EXACT_AUTHENTICATED_PR_SCOPE_REQUIRED')
    replacements = {
        "row['branch'] == 'recovery/rc6-material-fix-forward-3091e93-20261006'":
            "row['branch'] == " + repr(G5P_CANDIDATE_BRANCH),
        "row['PR'] == 476": "row['PR'] == " + str(G5P_CANDIDATE_PR),
    }
    for old, new in replacements.items():
        if builder.count(old) != 1:
            raise ValueError('G5P_BUILDER_EXACT_ORIGINAL_AUTHORITY_SITE_REQUIRED')
        builder = builder.replace(old, new)
    return builder

def prepare(source_root, output_root, *, python311, python312, owner_uid, read, save_raw,
            candidate_scope=None):
    if type(owner_uid) is not int or owner_uid <= 0 or not (os.getuid()==os.geteuid()==owner_uid):
        raise ValueError('DECLARED_DERIVATION_NONROOT_OWNER_REQUIRED')
    if os.getuid()!=owner_uid or os.geteuid()!=owner_uid:
        raise ValueError('DECLARED_DERIVATION_OWNER_REBOUND')
    output_root.mkdir(mode=0o700)
    originals={}
    for member,pin in PINS.items():
        wire=read(source_root/member)
        if hashlib.sha256(wire).hexdigest()!=pin:raise ValueError('IMMUTABLE_PREPARATION_DRIVER_CHANGED')
        originals[member]=wire
        save_raw(output_root/(Path(member).name+'.original'),wire)
    outer=originals[OUTER].decode()
    old="sys.platform == 'linux' and os.getuid() == os.geteuid() == 1000"
    if outer.count(old)!=1:raise ValueError('ORIGINAL_EXACT_BOOTSTRAP_UID_SITE_REQUIRED')
    outer=outer.replace(old,"sys.platform == 'linux' and os.getuid() == os.geteuid() == "+str(owner_uid))
    if outer.count('DECLARED_NATIVE_LINUX_NONROOT1000_REQUIRED')!=1:
        raise ValueError('ORIGINAL_BOOTSTRAP_SIGNATURE_REQUIRED')
    outer=outer.replace('DECLARED_NATIVE_LINUX_NONROOT1000_REQUIRED','DECLARED_NATIVE_LINUX_EXPLICIT_NONROOT_OWNER_REQUIRED')
    outer_path=output_root/'governed_outer.py'
    outer_sha=save_raw(outer_path,outer.encode())
    builder=originals[BUILDER].decode()
    replacements={
        "WRAPPER = Path('/workspace/scratch/rc6-full-gov-outer-preparation-2uy3dhqw/governed_outer.py')":'WRAPPER = Path('+repr(str(outer_path))+')',
        "WRAPPER_SHA = '7fa1aae01b863695e43f1b4de0b862fb8757281ea5506d791107651c5d057bc4'":'WRAPPER_SHA = '+repr(outer_sha),
        "OBJECTS = Path('/workspace/scratch/rc6-full-gov-outer-preparation-2uy3dhqw/original-required-19-objects.json')":'OBJECTS = Path('+repr(str(output_root/'original-required-19-objects.json'))+')',
        "'/workspace/scratch/rc6-readonly-3091e93-ofT80Pum/venv311/bin/python'":repr(str(python311)),
        "'/workspace/scratch/rc6-readonly-3091e93-ofT80Pum/venv312/bin/python'":repr(str(python312)),
    }
    for old,new in replacements.items():
        if builder.count(old)!=1:raise ValueError('EXACT_ORIGINAL_BUILDER_BINDING_SITE_REQUIRED')
        builder=builder.replace(old,new)
    # The original builder is immutable; only the exact candidate branch/number
    # constants can differ in the G5P candidate's authority predicate.
    builder=authority_literal_derivation(builder,candidate_scope)
    expected_functions_source=authority_literal_derivation(originals[BUILDER].decode(),candidate_scope)
    old_ast=ast.parse(expected_functions_source);new_ast=ast.parse(builder)
    functions=lambda node:{n.name:ast.dump(n) for n in node.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))}
    if functions(old_ast)!=functions(new_ast):raise ValueError('BUILDER_FUNCTION_BODY_CHANGED')
    outer_old=functions(ast.parse(originals[OUTER]));outer_new=functions(ast.parse(outer))
    if set(outer_old)!=set(outer_new) or any(outer_old[k]!=outer_new[k] for k in outer_old if k!='bootstrap_actual_kernel'):
        raise ValueError('OUTER_NONBOOTSTRAP_FUNCTION_CHANGED')
    builder_path=output_root/'build_governed_sources.py'
    builder_sha=save_raw(builder_path,builder.encode())
    objects_path=output_root/'original-required-19-objects.json'
    save_raw(objects_path,originals[OBJECTS])
    for name,old,new in [('builder',originals[BUILDER].decode(),builder),('outer',originals[OUTER].decode(),outer)]:
        save_raw(output_root/(name+'.declared-environment.diff'),''.join(difflib.unified_diff(old.splitlines(True),new.splitlines(True),fromfile=name+'.immutable',tofile=name+'.explicit-actions-binding')).encode())
    return {'builder_path':str(builder_path),'builder_sha256':builder_sha,'wrapper_path':str(outer_path),
        'wrapper_sha256':outer_sha,'objects_path':str(objects_path),'objects_sha256':PINS[OBJECTS],
        'declared_owner_uid':owner_uid,'original_pins':PINS,
        'scope':('EXACT_CANDIDATE_AUTHORITY_LITERAL_DERIVATION_ONLY' if candidate_scope is not None
            else 'NEW_EXTERNAL_BINDINGS_ONLY_NO_PRODUCT_OVERLAY'),
        'builder_all_function_AST_unchanged':candidate_scope is None,
        'builder_only_exact_candidate_authority_literals_changed':candidate_scope is not None,
        'candidate_authority_scope':candidate_scope,
        'outer_nonbootstrap_function_AST_unchanged':True}

