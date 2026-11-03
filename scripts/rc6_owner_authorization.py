"""Bounded GitHub issue-comment verification for RC6 deploy authorization."""

import json
import subprocess
import tempfile


# A180: the old 10 x 50 scan reached 500 comments on issue #624, making
# otherwise valid new exact-SHA approvals unreachable. GitHub supports 100
# comments/page; keep bounded traversal (5,000 total), with per-page byte cap.
PAGE_SIZE = 100
MAX_PAGES = 50
# A real 100-comment #624 page already exceeds 512 KiB.
MAX_PAGE_BYTES = 4 * 1024 * 1024


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
                        'RC6_624_APPROVE_DEPLOY_WITH_HOST_STRESS_DEFERRED',
                        'RC6_624_APPROVE_INTRADAY_SUSPEND_DEPLOY',
                        'RC6_624_APPROVE_PAPER_ADMIN_PREPARE',
                        'RC6_624_APPROVE_INTRADAY_PAPER_CARRYOVER',
                        'RC6_624_APPROVE_PAUSED_POSTCLOSE_WITH_CAUCION'}:
        raise ValueError('AUTHORIZATION_DECISION_INVALID')
    expected = f'{decision} candidate={candidate} tree={tree}'
    matches = []
    for page in range(1, MAX_PAGES + 1):
        url = f'repos/{repo}/issues/624/comments?per_page={PAGE_SIZE}&page={page}'
        raw = get_page(url)
        if len(raw) > MAX_PAGE_BYTES:
            raise ValueError('AUTHORIZATION_PAGE_BOUND')
        rows = json.loads(raw)
        if (type(rows) is not list or len(rows) > PAGE_SIZE or
                any(type(row) is not dict for row in rows)):
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
