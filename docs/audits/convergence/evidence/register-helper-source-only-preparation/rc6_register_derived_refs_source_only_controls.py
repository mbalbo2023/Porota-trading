#!/usr/bin/env python3
"""Controlled documentary codec fixture, not product/native/Gov evidence."""
import ast
import copy
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile

HELPER = Path('/tmp/rc6_remediation_register_generate.py')
SUPPLEMENT_HELPER = Path('/tmp/rc6_root_supplemental_named_findings_generate.py')
spec = importlib.util.spec_from_file_location('external_documentary_register_helper', HELPER)
H = importlib.util.module_from_spec(spec)
spec.loader.exec_module(H)
supp_spec = importlib.util.spec_from_file_location('external_documentary_supplement_helper', SUPPLEMENT_HELPER)
S = importlib.util.module_from_spec(supp_spec)
supp_spec.loader.exec_module(S)


def run():
    ast.parse(HELPER.read_bytes())
    ast.parse(SUPPLEMENT_HELPER.read_bytes())
    results = []
    with tempfile.TemporaryDirectory(prefix='rc6-derived-ref-controlled-') as tmp:
        repo = Path(tmp) / 'repo'
        repo.mkdir()
        def git(*args):
            return subprocess.check_output(['git', '--no-replace-objects', '--no-optional-locks', '-C', str(repo), *args], stderr=subprocess.PIPE)
        git('init', '-q')
        meta = {'schema': 'CONTROLLED_DOCUMENTARY_METADATA', 'scope': 'NO_PRODUCT_NO_GOV_NO_PROVIDER_NO_NATIVE_EXECUTION',
                'capture_source': 'CONTROLLED_PRIVATE_DOC_FIXTURE', 'environment': {'fixture_only': True},
                'counts': {'recorded_case_count': 2}, 'signed_zero': -0.0, 'null_clock': None, 'literal': 'á'}
        meta['literal_ref_shaped_data'] = {'$rc6_ref': {'schema': 'LITERAL_RAW_RECEIPT_DATA_NOT_CONTROL'}}
        raw_xml = b'<testsuites><testsuite name="controlled" tests="2"><testcase classname="tests.test_doc_fixture" name="test_doc" time="0"/><testcase classname="tests.test_doc_fixture" name="test_doc[x]" time="0"><skipped/></testcase></testsuite></testsuites>'
        raw_gzip = gzip.compress(raw_xml, mtime=0)
        meta['raw_refs'] = {'raw/cases.xml.gz': {'encoding': 'gzip-mtime0', 'sha256': H.sha256(raw_gzip),
            'bytes': len(raw_gzip), 'original_uncompressed_sha256': H.sha256(raw_xml),
            'original_uncompressed_bytes': len(raw_xml), 'decompressed_copy_byte_exact': True,
            'original_path': '/tmp/controlled-unread-capture.xml'}}
        raw_json = (json.dumps(meta, ensure_ascii=False) + '\n').encode()
        (repo / 'raw').mkdir()
        (repo / 'raw/meta.json').write_bytes(raw_json)
        (repo / 'raw/cases.xml').write_bytes(raw_xml)
        (repo / 'raw/cases.xml.gz').write_bytes(raw_gzip)
        (repo / 'tests').mkdir()
        (repo / 'tests/test_doc_fixture.py').write_text('def test_doc():\n    return None\n')
        original = {f'E{i:03d}': {'path': 'CONTROLLED_RECORD_ONLY', 'sha256': '0' * 64, 'bytes': i,
                    'source_scope': 'CONTROLLED_ORIGINAL_METADATA_NOT_RAW_EXECUTION',
                    'binding_source_sha': None, 'recorded_metadata': {'ordinal': i, 'signed_zero': -0.0, 'scope': 'CONTROLLED'},
                    'boundary': 'No product or native guard was executed.'} for i in range(1, 211)}
        original['E001'].update(path='raw/meta.json', sha256=H.sha256(raw_json), bytes=len(raw_json))
        original['E002'].update(path='raw/cases.xml', sha256=H.sha256(raw_xml), bytes=len(raw_xml))
        original['E003']['literal_ref_shaped_data'] = {'$rc6_ref': {'schema': 'LITERAL_ORIGINAL_CATALOG_DATA_NOT_CONTROL'}}
        original['E004'].update(path='raw/cases.xml.gz', sha256=H.sha256(raw_gzip), bytes=len(raw_gzip))
        template_path = repo / H.REGISTER_PATH
        template_path.parent.mkdir(parents=True)
        template_path.write_text(json.dumps({'schema': 'rc6.remediation-register.v1', 'evidence_catalog': original}))
        git('add', '.')
        git('-c', 'user.name=Controlled fixture', '-c', 'user.email=controlled@invalid', 'commit', '-q', '-m', 'Controlled documentary fixture only')
        sha = git('rev-parse', 'HEAD').decode().strip()
        source = H.Source(repo, sha)
        try:
            expanded = {'schema': 'rc6.remediation-register.v1', 'source_snapshot': {'sha': sha, 'tree': source.tree},
                'evidence_catalog': copy.deepcopy(original),
                'requirements': [{'id': 'CONTROLLED_NON_REQUIREMENT', 'GUARD': [{'base_node': 'tests/test_doc_fixture.py::test_doc',
                    'committed_source_binding': source.binding('tests/test_doc_fixture.py'),
                    'explicit_variants': ['[x]'], 'execution_evidence': [{'evidence_id': 'E002', 'recorded_status': 'SKIP', 'variant': '[x]'}]}]}],
                'additional_findings': [], 'restored_controls': [],
                'current_source_inventory': {'files': [source.binding('tests/test_doc_fixture.py')], 'guards': [{
                    'base_node': 'tests/test_doc_fixture.py::test_doc', 'committed_source_binding': source.binding('tests/test_doc_fixture.py'),
                    'execution_evidence': [{'evidence_id': 'E002', 'recorded_status': 'SKIP', 'variant': '[x]'}]}]},
                'current_path_evolution': {'tests/test_doc_fixture.py': {'committed_source_binding': source.binding('tests/test_doc_fixture.py')}},
                'validation': {'native_tests_executed': 0}}
            expanded['evidence_catalog']['E001'].update(current_publication_binding=source.binding('raw/meta.json'), original_raw_json_metadata=meta)
            expanded['evidence_catalog']['E002'].update(current_publication_binding=source.binding('raw/cases.xml'),
                original_raw_xml_metadata=H.xml_metadata_from_raw(raw_xml, source.entries))
            expanded['evidence_catalog']['E003']['previous_publication_definition'] = copy.deepcopy(original['E003'])
            decoded, encoding = H.decode_known_xml(raw_gzip, 'raw/cases.xml.gz')
            expanded['evidence_catalog']['E004'].update(current_publication_binding=source.binding('raw/cases.xml.gz'),
                original_raw_xml_metadata=H.xml_metadata_from_raw(decoded, source.entries), raw_encoding_metadata=encoding)
            compact = H.compact_derived(expanded, original, source)
            resolver = H.RefResolver(source, compact)
            reconstructed = resolver.expand()
            if H.canonical(reconstructed) != H.canonical(expanded):
                raise AssertionError('controlled roundtrip differs')
            if H.canonical({eid: compact['evidence_catalog'][eid] for eid in original}) != H.canonical(original):
                raise AssertionError('original210 fields changed')
            results.append({'control': 'ROUNDTRIP_ORIGINAL210_SIGNED_ZERO_NULL_SCOPE_AND_CLAUSE_VARIANTS', 'result': 'PRESERVED',
                            'resolved_refs': resolver.resolved_refs, 'verified_RAW_members': len(resolver.verified_members)})
            def reject(name, mutation):
                changed = copy.deepcopy(compact)
                mutation(changed)
                try:
                    H.RefResolver(source, changed).expand()
                except (H.RefResolutionError, ValueError, subprocess.SubprocessError):
                    results.append({'control': name, 'result': 'REJECTED'})
                else:
                    raise AssertionError('altered reference accepted: ' + name)
            ref_path = lambda d: d['current_evidence_derivation']['E001']['original_raw_json_metadata']['$rc6_ref']
            reject('RAW_BYTE_DIGEST_MISMATCH', lambda d: ref_path(d)['member'].update(sha256='f' * 64))
            reject('RAW_GIT_MODE_MISMATCH', lambda d: ref_path(d)['member'].update(git_mode='100755'))
            reject('RAW_TREE_MISMATCH', lambda d: ref_path(d)['member'].update(source_tree='f' * 40))
            reject('ABBREVIATED_RAW_SOURCE_SHA', lambda d: ref_path(d)['member'].update(source_sha=sha[:8]))
            reject('RAW_LENGTH_BOOL_NOT_INT', lambda d: ref_path(d)['member'].update(bytes=True))
            reject('RAW_PATH_ESCAPE', lambda d: ref_path(d)['member'].update(path='../raw/meta.json'))
            reject('DERIVED_METADATA_HASH_MISMATCH', lambda d: ref_path(d).update(value_sha256='f' * 64))
            reject('UNKNOWN_REFERENCE_KIND', lambda d: ref_path(d).update(kind='UNKNOWN'))
            reject('FALSE_NEW_EXECUTION_AUTHORITY', lambda d: ref_path(d).update(new_execution_claimed=True))
            reject('ORIGINAL210_FIELD_MUTATION', lambda d: d['evidence_catalog']['E100'].update(boundary='changed'))
            reject('ORIGINAL210_ID_SUBSTITUTION', lambda d: d['original_catalog_preservation']['ids'].__setitem__(-1, 'E211'))
            reject('ABSENT_LOCAL_PAYLOAD_KEY', lambda d: d['current_source_inventory']['files'][0]['$rc6_ref'].update(key='missing'))
            def mutate_table(d):
                ref = d['current_source_inventory']['files'][0]['$rc6_ref']
                d['derived_payload_tables'][ref['table']][ref['key']]['git_mode'] = '100755'
            reject('LOCAL_PAYLOAD_HASH_MISMATCH', mutate_table)
            reject('DOCUMENT_SOURCE_SNAPSHOT_MISMATCH', lambda d: d['source_snapshot'].update(sha='f' * 40))
            gzip_ref = lambda d: d['current_evidence_derivation']['E004']['original_raw_xml_metadata']['$rc6_ref']
            reject('GZIP_UNCOMPRESSED_HASH_MISMATCH', lambda d: gzip_ref(d)['xml_encoding'].update(uncompressed_sha256='f' * 64))
            reject('GZIP_COMPRESSED_LENGTH_MISMATCH', lambda d: gzip_ref(d)['xml_encoding'].update(compressed_bytes=len(raw_gzip) + 1))
            reject('GZIP_MTIME_FALSE_DECLARATION', lambda d: gzip_ref(d)['xml_encoding'].update(gzip_mtime=1))
            reject('ABSENT_GZIP_RAW_MEMBER_NOT_ZERO_PASS', lambda d: gzip_ref(d)['member'].update(path='raw/missing.xml.gz'))
            def rejects_decoder(name, data, path):
                for module in (H, S):
                    try:
                        module.decode_known_xml(data, path)
                    except ValueError:
                        continue
                    raise AssertionError('altered XML/gzip was accepted: ' + name)
                results.append({'control': name, 'result': 'REJECTED_BY_BOTH_STANDALONE_HELPERS'})
            bad_crc = bytearray(raw_gzip)
            bad_crc[-8] ^= 1
            rejects_decoder('GZIP_CRC_CHANGED', bytes(bad_crc), 'raw/cases.xml.gz')
            rejects_decoder('GZIP_TRUNCATED_TRAILER', raw_gzip[:-4], 'raw/cases.xml.gz')
            rejects_decoder('GZIP_CONCATENATED_MEMBERS', raw_gzip + raw_gzip, 'raw/cases.xml.gz')
            rejects_decoder('GZIP_UNDECLARED_TRAILING_BYTES', raw_gzip + b'trailing', 'raw/cases.xml.gz')
            rejects_decoder('ARBITRARY_ZIP_MEMBER_UNSUPPORTED', b'PK\x03\x04' + raw_xml, 'raw/cases.zip')
            refs = H.declared_xml_receipt_members(source, meta)
            if H.canonical(refs) != H.canonical(S.declared_xml_receipt_members(source, meta)):
                raise AssertionError('standalone supplement/register XML declarations differ')
            if len(refs) != 1 or refs[0]['xml_encoding'] != encoding or refs[0]['raw_xml_metadata']['actual_case_count'] != 2:
                raise AssertionError('gzip native-shaped fixture count or encoding was lost')
            results.append({'control': 'KNOWN_GZIP_MTIME0_EXACT_RAW_SCOPES_AND_TWO_CASES', 'result': 'PRESERVED_WITHOUT_NATIVE_EXECUTION',
                'compressed_bytes': encoding['compressed_bytes'], 'uncompressed_bytes': encoding['uncompressed_bytes'],
                'recorded_case_outcomes': refs[0]['raw_xml_metadata']['actual_case_outcomes']})
            def rejects_declaration(name, mutation):
                changed = copy.deepcopy(meta)
                mutation(changed['raw_refs'])
                for module in (H, S):
                    try:
                        module.declared_xml_receipt_members(source, changed)
                    except (ValueError, FileNotFoundError):
                        continue
                    raise AssertionError('invalid XML declaration accepted: ' + name)
                results.append({'control': name, 'result': 'REJECTED_BY_BOTH_STANDALONE_HELPERS'})
            rejects_declaration('DECLARED_GZIP_RAW_HASH_CHANGED', lambda r: r['raw/cases.xml.gz'].update(sha256='f' * 64))
            rejects_declaration('DECLARED_GZIP_UNCOMPRESSED_HASH_CHANGED', lambda r: r['raw/cases.xml.gz'].update(original_uncompressed_sha256='f' * 64))
            rejects_declaration('DECLARED_GZIP_LENGTH_BOOL', lambda r: r['raw/cases.xml.gz'].update(bytes=True))
            rejects_declaration('DECLARED_GZIP_MISSING_PATH_NOT_ZERO_PASS', lambda r: r.update({'raw/missing.xml.gz': r.pop('raw/cases.xml.gz')}))
            compact_path = Path(tmp) / 'controlled-compact.json'
            compact_path.write_text(json.dumps(compact, ensure_ascii=False, allow_nan=False))
            cli = subprocess.run([os.environ.get('RC6_DIAGNOSTIC_PYTHON', os.sys.executable), str(HELPER), '--repo', str(repo),
                '--source', sha, '--verify-refs', str(compact_path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}, check=True, timeout=5)
            verified_cli = json.loads(cli.stdout)
            if verified_cli.get('status') != 'DERIVED_REFERENCES_VERIFIED_NOT_EXECUTED' or verified_cli.get('native_tests_executed') != 0:
                raise AssertionError('controlled CLI granted execution authority')
            results.append({'control': 'CONTROLLED_EXTERNAL_COMPACT_CLI_VERIFICATION', 'result': 'VERIFIED_DOCUMENTARY_REFS_ONLY',
                'resolved_refs': verified_cli['resolved_refs'], 'verified_RAW_members': verified_cli['verified_RAW_members']})
        finally:
            source.close()
    receipt = {'schema': 'rc6.derived-register-ref-controlled-diagnostic.v1',
        'scope': 'PRIVATE_SYNTHETIC_GIT_JSON_XML_CODEC_ONLY_NO_PRODUCT_IMPORT_NO_NATIVE_GOV_PROVIDER_IMAGE_RUNTIME_OR_FINANCIAL_EXECUTION',
        'helper_sha256': hashlib.sha256(HELPER.read_bytes()).hexdigest(), 'controls': results,
        'supplement_helper_sha256': hashlib.sha256(SUPPLEMENT_HELPER.read_bytes()).hexdigest(),
        'driver_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'control_count': len(results), 'native_tests_executed': 0, 'root_files_modified': False,
        'generator26MB_executed': False, 'claim_boundary': 'Controlled parser/codec cases are not native product regression, real final receipt or final freeze.'}
    target = Path('/tmp/rc6_register_derived_refs_source_only_controls.json')
    target.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'receipt': str(target), 'sha256': hashlib.sha256(target.read_bytes()).hexdigest(), 'controls': len(results),
        'scope': receipt['scope'], 'native_tests_executed': 0}))


if __name__ == '__main__':
    run()
