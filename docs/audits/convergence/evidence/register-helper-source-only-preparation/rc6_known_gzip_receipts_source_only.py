#!/usr/bin/env python3
"""Immutable Git/gzip/XML metadata only: never run native or product code."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

HELPER = Path('/tmp/rc6_remediation_register_generate.py')
RECEIPT_PATH = 'docs/audits/rc6-convergence-persistence-evidence/source_capture_deadline/checkpoint.json'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--source', required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location('documentary_only_helper', HELPER)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    source = helper.Source(args.repo, args.source)
    try:
        target = helper.outside_output(source, args.out)
        receipt = source.json(RECEIPT_PATH)
        members = helper.declared_xml_receipt_members(source, receipt)
        rows = [{key: row[key] for key in ('publication_binding', 'receipt_declaration_pointer',
            'declared_reference_verbatim', 'xml_encoding', 'case_count_scope')} | {
            'recorded_case_count': row['raw_xml_metadata']['actual_case_count'],
            'recorded_case_outcomes': row['raw_xml_metadata']['actual_case_outcomes'],
            'raw_xml_metadata_sha256': helper.sha256(helper.canonical(row['raw_xml_metadata']))
        } for row in members]
        output = {'schema': 'rc6.known-gzip-receipt-source-only-diagnostic.v1',
            'scope': 'IMMUTABLE_GIT_XML_METADATA_READ_ONLY_NO_NEW_NATIVE_GOV_RUNTIME_IMAGE_OR_FINANCIAL_EXECUTION',
            'source_sha': source.sha, 'source_tree': source.tree,
            'helper_sha256': helper.sha256(HELPER.read_bytes()),
            'driver_sha256': helper.sha256(Path(__file__).read_bytes()),
            'parent_receipt_binding': source.binding(RECEIPT_PATH),
            'parent_scope_verbatim': receipt['scope'],
            'OWN_fixed_execution_scope_verbatim': receipt['validation']['execution_scope'],
            'OWN_fixed_source_sha_verbatim': receipt['validation']['whole_fix_source_sha'],
            'OWN_fixed_source_tree_verbatim': receipt['validation']['whole_fix_source_tree'],
            'OWN_fixed_declared_native_case_count_verbatim': receipt['validation']['fix_native_tests'],
            'original_source_sha_verbatim': receipt['original_whole_source_sha'],
            'original_source_tree_verbatim': receipt['original_whole_tree'],
            'xml_members': rows, 'native_tests_executed_by_this_diagnostic': 0,
            'generator26MB_executed': False, 'root_files_modified': False,
            'counts_summed': False, 'final_source_or_material_acceptance_claimed': False}
    finally:
        source.close()
    target.write_text(json.dumps(output, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'out': str(target), 'bytes': target.stat().st_size,
        'sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
        'members': [(r['publication_binding']['path'].rsplit('/', 1)[-1], r['recorded_case_count'],
                     r['recorded_case_outcomes']) for r in rows], 'native_tests_executed': 0}))


if __name__ == '__main__':
    main()
