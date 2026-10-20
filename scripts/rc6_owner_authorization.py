"""Bounded GitHub issue-comment verification for RC6 deploy authorization."""

import json
import subprocess
import tempfile


PAGE_SIZE = 10
MAX_PAGES = 50
MAX_PAGE_BYTES = 512 * 1024


def github_issue_page(url):
    with tempfile.TemporaryFile() as output:
        subprocess.run(['gh', 'api', url], stdout=output,
                       stderr=subprocess.DEVNULL, timeout=25, check=True)
        output.seek(0)
        return output.read(MAX_PAGE_BYTES + 1)


def require_exact_owner_authorization(repo, candidate, tree, *,
                                      decision='RC6_624_APPROVE_DEPLOY_AND_COLD2',
                                      get_page=github_issue_page):
    if decision not in {'RC6_624_APPROVE_DEPLOY_AND_COLD2',
                        'RC6_624_APPROVE_DEPLOY_WITH_HOST_STRESS_DEFERRED'}:
        raise ValueError('AUTHORIZATION_DECISION_INVALID')
    expected = f'{decision} candidate={candidate} tree={tree}'
    matches = []
    for page in range(1, MAX_PAGES + 1):
        url = f'repos/{repo}/issues/624/comments?per_page={PAGE_SIZE}&page={page}'
        raw = get_page(url)
        if len(raw) > MAX_PAGE_BYTES:
            raise ValueError('AUTHORIZATION_PAGE_BOUND')
        rows = json.loads(raw)
        if type(rows) is not list:
            raise ValueError('AUTHORIZATION_RESPONSE_INVALID')
        matches.extend(row for row in rows if
                       (row.get('user') or {}).get('login') == 'mbalbo2023' and
                       row.get('body', '').strip() == expected and
                       row.get('created_at') == row.get('updated_at'))
        if len(rows) < PAGE_SIZE:
            break
    else:
        raise ValueError('AUTHORIZATION_PAGINATION_BOUND')
    if len(matches) != 1:
        raise ValueError('EXACT_POSTERIOR_OWNER_AUTHORIZATION_REQUIRED')
    return matches[0]['id']
