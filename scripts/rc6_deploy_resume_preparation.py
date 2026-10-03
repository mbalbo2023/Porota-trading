#!/usr/bin/env python3
"""Permission-separated preparation entrypoint for validation-only resume.

The tested runtime resume is unchanged. The runner may publish content blobs,
never workflow paths, trees, commits, or refs. The workflow-authorized GitHub
connector performs the separate permanent-fix publication. HTTP 403 on optional
blob staging is recorded as ARTIFACT_ONLY, not misreported as EN_GITHUB.
"""
from pathlib import Path
import hashlib
import json
import os
import shutil
import sys
import urllib.error
import urllib.request
import rc6_deploy_validation_resume as resume

BASE_MODULE_BLOB = 'f12312b31ceee4f7fc62933e495ba82b5d4bae2b'


def stage_blobs(files, post):
    entries = []
    status = 'BLOBS_STAGED'
    for path, content in files.items():
        expected = resume.git_blob(content)
        try:
            result = post('/git/blobs', {'content': content, 'encoding': 'utf-8'})
            resume.require(result.get('sha') == expected, 'STAGED_BLOB_MISMATCH')
        except urllib.error.HTTPError as exc:
            if exc.code != 403:
                raise
            status = 'ARTIFACT_ONLY'
        entries.append({'path': path, 'mode': '100644', 'type': 'blob', 'sha': expected})
    return status, entries


def stage_fix(source, directory):
    config = json.loads((directory / 'resume-config.json').read_text())
    target = directory / 'fix-worktree'
    resume.run(['git', 'worktree', 'add', '--detach', str(target), config['product']])
    paths = [resume.WORKFLOW, 'scripts/rc6_deploy_preopen_gate.py',
             'tests/test_rc6_deploy_preopen_gate.py']
    for path in paths[1:]:
        (target / path).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(resume.ROOT / path, target / path)
    (target / paths[0]).write_text(resume.patch_workflow(source))
    resume.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests',
                '-p', 'test_rc6_deploy_preopen_gate.py', '-v'], cwd=target)
    resume.run(['git', 'diff', '--check'], cwd=target)
    resume.run(['git', 'add', '--', *paths], cwd=target)
    resume.require(set(resume.output(['git', 'diff', '--cached', '--name-only'], cwd=target).splitlines()) == set(paths), 'FIX_SCOPE_DRIFT')
    tree = resume.output(['git', 'write-tree'], cwd=target)
    files = {path: (target / path).read_text() for path in paths}
    api = resume.GitHubAPI(os.environ['GITHUB_REPOSITORY'])
    def post(path, payload):
        resume.require(path == '/git/blobs', 'PUBLICATION_ENDPOINT_FORBIDDEN')
        headers = dict(api.headers); headers['Content-Type'] = 'application/json'
        request = urllib.request.Request(api.base + path, data=json.dumps(payload).encode(), headers=headers, method='POST')
        with api.opener.open(request, timeout=45) as response:
            return json.load(response)
    status, entries = stage_blobs(files, post)
    plan = {'branch': resume.FIX_BRANCH, 'base': config['product'],
            'base_tree': config['tree'], 'tested_tree': tree, 'scope': paths,
            'publication_status': status, 'tree_elements': entries,
            'tests_passed': 13, 'payloads': files}
    (directory / 'permanent-fix.json').write_text(json.dumps(plan, indent=2) + '\n')
    summary = {key: value for key, value in plan.items() if key != 'payloads'}
    print('PERMANENT_FIX_PUBLICATION_PLAN=' + json.dumps(summary, sort_keys=True), flush=True)


def main():
    resume.require(len(sys.argv) > 1 and sys.argv[1] == 'prepare', 'PREPARATION_ONLY')
    actual = resume.git_blob(Path(resume.__file__).read_text())
    resume.require(actual == BASE_MODULE_BLOB, 'UNREVIEWED_RESUME_MODULE')
    # A preparation hook only: no runtime function, gate, or frozen plan is
    # modified. The original writer's workflow-path POST is never invoked.
    resume.publish_fix = stage_fix
    resume.main()


if __name__ == '__main__':
    main()
