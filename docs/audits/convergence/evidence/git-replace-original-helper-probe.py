#!/usr/bin/env python3
"""Offline Git-helper probe only; no Gov, product runtime or financial claim."""
import ast
import base64
import hashlib
import json
import os
from pathlib import Path
import resource
import subprocess
import tempfile
import time

SOURCE_ROOT = Path('/workspace/porota_rc6_convergence')
SOURCE_SHA = '107a83197a5f80e299fbe63c9b5ef27f853945be'
OUTPUT = Path('/tmp/rc6_git_replace_original_helper_probe.json')

def object_id(kind, value):
    return hashlib.sha1(kind.encode() + b' ' + str(len(value)).encode() + b'\0' + value).hexdigest()

def native(repo, *args, data=None):
    return subprocess.check_output(['git', '--no-replace-objects', '-C', str(repo), *args],
                                   input=data, stderr=subprocess.PIPE)

def load_original(path, selected):
    source = native(SOURCE_ROOT, 'show', SOURCE_SHA + ':' + path)
    record = native(SOURCE_ROOT, 'ls-tree', SOURCE_SHA, '--', path).decode().strip()
    mode, kind, blob = record.partition('\t')[0].split()
    assert kind == 'blob' and mode in {'100644', '100755'}
    assert object_id('blob', source) == blob
    parsed = ast.parse(source, filename=path)
    statements = [node for node in parsed.body
                  if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in selected]
    assert len(statements) == len(selected)
    code = '\n\n'.join(ast.get_source_segment(source.decode(), node) for node in statements) + '\n'
    namespace = {'subprocess': subprocess, 'Path': Path}
    exec(compile(code, SOURCE_SHA + ':' + path, 'exec'), namespace)
    return namespace, {'path': path, 'git_mode': mode, 'blob': blob,
                       'file_sha256': hashlib.sha256(source).hexdigest(),
                       'file_bytes': len(source), 'selected_nodes': sorted(selected),
                       'executed_segment_sha256': hashlib.sha256(code.encode()).hexdigest(),
                       'extraction': 'EXACT_AST_SOURCE_SEGMENTS_WITH_ORIGINAL_FUNCTION_BODIES'}

def main():
    started = time.monotonic()
    fip, fip_meta = load_original('scripts/rc6_convergence_provenance.py',
                                {'ConvergenceError', 'require', 'git', 'tree'})
    gate, gate_meta = load_original('scripts/rc6_issue465_audit_gate.py',
                                  {'AuditGateError', 'require', 'git', '_source_record'})
    artifact, artifact_meta = load_original('scripts/porota_artifact_provenance.py',
                                          {'ProvenanceError', '_git'})
    private = Path(tempfile.mkdtemp(prefix='rc6-git-replace-helper-only-'))
    native(private, 'init', '--quiet', '--bare', '--initial-branch=probe')
    env = dict(os.environ, GIT_AUTHOR_NAME='Offline helper probe',
               GIT_AUTHOR_EMAIL='offline@example.invalid',
               GIT_COMMITTER_NAME='Offline helper probe',
               GIT_COMMITTER_EMAIL='offline@example.invalid',
               GIT_AUTHOR_DATE='2026-10-05T00:00:00+00:00',
               GIT_COMMITTER_DATE='2026-10-05T00:00:00+00:00')
    commits = []
    for value in (b'ORIGINAL_PRIVATE_CONTROL\n', b'REPLACEMENT_PRIVATE_CONTROL\n'):
        blob = native(private, 'hash-object', '-w', '--stdin', data=value).decode().strip()
        tree = native(private, 'mktree', data=('100644 blob ' + blob + '\tvalue.txt\n').encode()).decode().strip()
        commit = subprocess.check_output(['git', '--no-replace-objects', '-C', str(private),
                                          'commit-tree', tree, '-m', value.decode().strip()],
                                         env=env, stderr=subprocess.PIPE).decode().strip()
        raw = native(private, 'cat-file', 'commit', commit)
        assert object_id('commit', raw) == commit
        commits.append({'sha': commit, 'tree': tree, 'blob': blob,
                        'raw_commit_base64': base64.b64encode(raw).decode(),
                        'value_base64': base64.b64encode(value).decode()})
    native(private, 'update-ref', 'refs/heads/probe', commits[0]['sha'])
    checks = []
    for role, original, replacement in (('candidate', commits[0], commits[1]),
                                       ('source', commits[1], commits[0])):
        requested = original['sha']
        native(private, 'update-ref', 'refs/replace/' + requested, replacement['sha'])
        control_raw = native(private, 'cat-file', 'commit', requested)
        fip_raw = fip['git'](private, 'cat-file', 'commit', requested, binary=True)
        artifact_raw = artifact['_git'](private, 'cat-file', 'commit', requested)
        gate_raw = gate['git'](private, 'cat-file', 'commit', requested).encode() + b'\n'
        helper_tree = fip['tree'](private, requested)
        helper_gate = gate['_source_record'](private, requested, 'value.txt')
        helper_value = fip['git'](private, 'show', requested + ':value.txt', binary=True)
        raw_original_sha = object_id('commit', control_raw)
        interpreted_sha = object_id('commit', fip_raw)
        assert raw_original_sha == requested
        assert interpreted_sha == replacement['sha'] and interpreted_sha != requested
        assert artifact_raw == fip_raw and gate_raw == fip_raw
        assert helper_tree['value.txt']['blob'] == replacement['blob']
        assert helper_gate['blob'] == replacement['blob']
        assert native(private, 'rev-parse', requested + '^{tree}').decode().strip() == original['tree']
        assert helper_value == base64.b64decode(replacement['value_base64'])
        checks.append({'role': role, 'requested_sha': requested,
                       'replacement_ref': 'refs/replace/' + requested,
                       'replacement_target_sha': replacement['sha'],
                       'original_helper_commit_sha1': interpreted_sha,
                       'raw_no_replace_commit_sha1': raw_original_sha,
                       'original_helpers_agree_on_replaced_commit': True,
                       'original_helper_tree_blob': helper_tree['value.txt']['blob'],
                       'original_gate_source_record_blob': helper_gate['blob'],
                       'raw_original_tree': original['tree'],
                       'raw_original_blob': original['blob'],
                       'original_helper_value_base64': base64.b64encode(helper_value).decode(),
                       'negative_control': 'RED_ORIGINAL_HELPER_OBJECT_IDENTITY_FOLLOWS_REPLACEMENT',
                       'positive_control': 'GREEN_NATIVE_NO_REPLACE_PRESERVES_RAW_OBJECT_IDENTITY'})
        native(private, 'update-ref', '-d', 'refs/replace/' + requested)
        assert fip['git'](private, 'cat-file', 'commit', requested, binary=True) == control_raw
    assert not native(private, 'for-each-ref', 'refs/replace')
    elapsed = time.monotonic() - started
    script_bytes = Path(__file__).read_bytes()
    report = {'schema': 'rc6.git-replace-original-helper-only.v1', 'status': 'PROBE_COMPLETE',
              'source_sha': SOURCE_SHA, 'source_read_strategy': 'GIT_NO_REPLACE_FIXED_COMMITTED_BLOBS',
              'source_helpers': [fip_meta, gate_meta, artifact_meta],
              'private_repository': str(private), 'commits': commits, 'checks': checks,
              'native_checks': 2, 'original_negative_controls': 2, 'raw_positive_controls': 2,
              'replacement_refs_removed_after_probe': True,
              'elapsed_seconds_including_original_source_reads_and_setup': elapsed,
              'cpu_seconds_self': resource.getrusage(resource.RUSAGE_SELF).ru_utime + resource.getrusage(resource.RUSAGE_SELF).ru_stime,
              'cpu_seconds_children': resource.getrusage(resource.RUSAGE_CHILDREN).ru_utime + resource.getrusage(resource.RUSAGE_CHILDREN).ru_stime,
              'script_path': str(Path(__file__)), 'script_sha256': hashlib.sha256(script_bytes).hexdigest(),
              'scope': 'AST_EXTRACTED_ORIGINAL_GIT_HELPERS_ONLY_WITH_TWO_PRIVATE_SYNTHETIC_COMMITS',
              'limitations': ['NOT_FULL_FIP_OR_GATE_EXECUTION', 'NOT_GOVERNED_TEST_RECEIPT',
                              'NOT_FINANCIAL_OR_RUNTIME_EVIDENCE', 'NO_NETWORK',
                              'NO_ROOT_WRITES', 'NO_NEW_ORIGINAL_SCENARIO_OR_ATTACK_ID'],
              'root_files_edited': [], 'root_git_mutations': []}
    OUTPUT.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
    print(json.dumps({'report': str(OUTPUT), 'report_sha256': hashlib.sha256(OUTPUT.read_bytes()).hexdigest(),
                      'script_sha256': report['script_sha256'], 'elapsed_seconds': elapsed,
                      'negative_controls': 2, 'positive_controls': 2,
                      'scope': report['scope']}))

if __name__ == '__main__':
    main()
