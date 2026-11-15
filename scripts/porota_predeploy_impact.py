#!/usr/bin/env python3
"""Skip image work only for an exact, validated Markdown-only Git diff."""
from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath
import posixpath
import re
import subprocess
from urllib.parse import unquote, urlsplit


def _git(repository, *arguments):
    try:
        return subprocess.check_output(['git', '-C', str(repository), *arguments],
                                       stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError:
        raise ValueError('IMPACT_GIT_QUERY_FAILED') from None


def _document_links(text):
    """Check file destinations in inline links and reference definitions."""
    lines, fence = [], None
    for line in text.splitlines():
        marker = re.match(r'^\s*(`{3,}|~{3,})', line)
        if marker:
            token = marker[1]
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
            continue
        if fence is None:
            lines.append(line)
    prose = re.sub(r'`+[^`]*`+', '', '\n'.join(lines))
    inline = re.findall(r'!?\[[^\]\n]*\]\(\s*(<[^>]*>|[^\s)]+)(?:\s+[\'\"][^)]*[\'\"])?\s*\)', prose)
    references = re.findall(r'^\s*\[[^\]\n]+\]:\s*(<[^>]*>|\S+)', prose, re.MULTILINE)
    return [value.strip('<>') for value in inline + references]


def classify(repository, base_sha, head_sha):
    repository = Path(repository)
    if not all(isinstance(value, str) and re.fullmatch(r'[0-9a-f]{40}', value)
               for value in (base_sha, head_sha)):
        raise ValueError('IMPACT_SHA_INVALID')
    for value in (base_sha, head_sha):
        if _git(repository, 'rev-parse', '--verify', value + '^{commit}').decode().strip() != value:
            raise ValueError('IMPACT_COMMIT_MISMATCH')
    if _git(repository, 'rev-parse', 'HEAD').decode().strip() != head_sha:
        raise ValueError('IMPACT_CHECKOUT_MISMATCH')
    if _git(repository, 'status', '--porcelain', '--untracked-files=all'):
        raise ValueError('IMPACT_CHECKOUT_DIRTY')
    # --no-renames preserves deletion of a non-Markdown source even if the
    # same bytes reappear with a .md suffix. Compare exact trees, not merge-base.
    changed = sorted(set(_git(repository, 'diff', '--name-only', '--no-renames', '-z',
                              base_sha, head_sha, '--').decode().rstrip('\0').split('\0')))
    if changed == ['']:
        raise ValueError('IMPACT_EMPTY_DIFF')
    docs_only = all(PurePosixPath(path).suffix.lower() == '.md' for path in changed)
    report = {'schema': 'porota.predeploy.impact.v1', 'base_sha': base_sha, 'head_sha': head_sha,
              'head_tree': _git(repository, 'rev-parse', head_sha + '^{tree}').decode().strip(),
              'classification': 'DOCUMENTATION_ONLY' if docs_only else 'APPLICATION_RELEASE',
              'changed_paths': changed, 'documentation_validated': False, 'local_links_checked': 0}
    if docs_only:
        entries = {}
        for item in _git(repository, 'ls-tree', '-r', '-t', '-z', head_sha).split(b'\0'):
            if item:
                metadata, path = item.split(b'\t', 1)
                mode, kind, blob = metadata.decode().split()
                entries[path.decode()] = (mode, kind, blob)
        for path in changed:
            if path not in entries:  # An explicitly removed document has no head blob.
                continue
            mode, kind, blob = entries[path]
            if mode not in {'100644', '100755'} or kind != 'blob':
                raise ValueError('DOCUMENT_NOT_REGULAR')
            text = _git(repository, 'cat-file', 'blob', blob).decode('utf-8')
            if '\0' in text or re.search(r'^(?:<{7}|>{7})(?:\s|$)', text, re.MULTILINE):
                raise ValueError('DOCUMENT_CONFLICT_OR_INVALID_TEXT')
            for link in _document_links(text):
                target = urlsplit(link)
                if target.scheme or target.netloc or not target.path:
                    continue
                decoded = unquote(target.path)
                local = posixpath.normpath(decoded.lstrip('/') if decoded.startswith('/') else
                                          posixpath.join(posixpath.dirname(path), decoded))
                if local == '..' or local.startswith('../') or (local != '.' and local not in entries):
                    raise ValueError('DOCUMENT_LINK_MISSING:' + path + ':' + link)
                report['local_links_checked'] += 1
        report['documentation_validated'] = True
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository', type=Path, default=Path.cwd())
    parser.add_argument('--base-sha', required=True)
    parser.add_argument('--head-sha', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--github-output', type=Path)
    arguments = parser.parse_args()
    report = classify(arguments.repository, arguments.base_sha, arguments.head_sha)
    arguments.output.write_text(json.dumps(report, sort_keys=True) + '\n')
    if arguments.github_output:
        with arguments.github_output.open('a') as output:
            for field in ('base_sha', 'head_sha', 'head_tree', 'classification', 'documentation_validated'):
                value = str(report[field]).lower() if isinstance(report[field], bool) else report[field]
                output.write(field + '=' + value + '\n')
    print(json.dumps(report, sort_keys=True))


if __name__ == '__main__':
    main()
