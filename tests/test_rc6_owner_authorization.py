import json
import unittest

from scripts.rc6_owner_authorization import (
    MAX_PAGE_BYTES, MAX_PAGES, PAGE_SIZE, require_exact_owner_authorization,
)


class OwnerAuthorizationTests(unittest.TestCase):
    def test_authorization_after_more_than_five_hundred_comments_is_found(self):
        candidate, tree = 'a' * 40, 'b' * 40
        expected = f'RC6_624_APPROVE_DEPLOY_AND_COLD2 candidate={candidate} tree={tree}'
        comments = [dict(id=n, body='x' * 10000, user={'login': 'mbalbo2023'},
                         created_at='2026-10-19T00:00:00Z',
                         updated_at='2026-10-19T00:00:00Z') for n in range(506)]
        comments.append(dict(id=6281817812, body=expected,
                             user={'login': 'mbalbo2023'},
                             created_at='2026-10-20T02:20:09Z',
                             updated_at='2026-10-20T02:20:09Z'))
        self.assertGreater(len(json.dumps(comments).encode()), MAX_PAGE_BYTES)
        requests = []

        def page(url):
            requests.append(url)
            n = int(url.rsplit('page=', 1)[1])
            return json.dumps(comments[(n - 1) * PAGE_SIZE:n * PAGE_SIZE]).encode()

        self.assertEqual(require_exact_owner_authorization(
            'owner/repo', candidate, tree, get_page=page), 6281817812)
        self.assertEqual(len(requests), 6)
        self.assertTrue(all('per_page=100' in url for url in requests))

    def test_authorization_rejects_duplicate_or_edited_approval(self):
        candidate, tree = 'a' * 40, 'b' * 40
        body = f'RC6_624_APPROVE_DEPLOY_AND_COLD2 candidate={candidate} tree={tree}'
        approval = dict(id=1, body=body, user={'login': 'mbalbo2023'},
                        created_at='t1', updated_at='t1')
        def page(rows):
            return lambda _: json.dumps(rows).encode()

        with self.assertRaisesRegex(ValueError, 'EXACT_POSTERIOR_OWNER_AUTHORIZATION_REQUIRED'):
            require_exact_owner_authorization('owner/repo', candidate, tree,
                                              get_page=page([approval, dict(approval, id=2)]))
        with self.assertRaisesRegex(ValueError, 'EXACT_POSTERIOR_OWNER_AUTHORIZATION_REQUIRED'):
            require_exact_owner_authorization('owner/repo', candidate, tree,
                                              get_page=page([dict(approval, updated_at='t2')]))

    def test_authorization_keeps_page_size_guard(self):
        with self.assertRaisesRegex(ValueError, 'AUTHORIZATION_PAGE_BOUND'):
            require_exact_owner_authorization('owner/repo', 'a' * 40, 'b' * 40,
                                              get_page=lambda _: b'x' * (MAX_PAGE_BYTES + 1))

    def test_authorization_remains_bounded_after_five_thousand_comments(self):
        candidate, tree = 'a' * 40, 'b' * 40
        row = {'id': 1, 'body': 'not an approval',
               'user': {'login': 'mbalbo2023'},
               'created_at': 't1', 'updated_at': 't1'}
        seen = []
        def full_page(url):
            seen.append(url)
            return json.dumps([row] * PAGE_SIZE).encode()
        with self.assertRaisesRegex(ValueError, 'AUTHORIZATION_PAGINATION_BOUND'):
            require_exact_owner_authorization(
                'owner/repo', candidate, tree, get_page=full_page)
        self.assertEqual(len(seen), MAX_PAGES)

    def test_authorization_rejects_oversized_or_malformed_page(self):
        candidate, tree = 'a' * 40, 'b' * 40
        row = {'id': 1, 'body': 'not an approval',
               'user': {'login': 'mbalbo2023'},
               'created_at': 't1', 'updated_at': 't1'}
        for malformed in ([row] * (PAGE_SIZE + 1), [None], {'message': 'bad'}):
            with self.subTest(malformed_type=type(malformed).__name__), self.assertRaisesRegex(
                    ValueError, 'AUTHORIZATION_RESPONSE_INVALID'):
                require_exact_owner_authorization(
                    'owner/repo', candidate, tree,
                    get_page=lambda _: json.dumps(malformed).encode())

    def test_intraday_suspend_requires_distinct_exact_owner_decision(self):
        candidate, tree = 'e' * 40, 'f' * 40
        body = (f'RC6_624_APPROVE_INTRADAY_SUSPEND_DEPLOY '
                f'candidate={candidate} tree={tree}')
        row = dict(id=3, body=body, user={'login': 'mbalbo2023'},
                   created_at='t1', updated_at='t1')
        page = lambda rows: lambda _: json.dumps(rows).encode()
        with self.assertRaisesRegex(ValueError, 'EXACT_POSTERIOR_OWNER_AUTHORIZATION_REQUIRED'):
            require_exact_owner_authorization('owner/repo', candidate, tree,
                decision='RC6_624_APPROVE_INTRADAY_SUSPEND_DEPLOY',
                get_page=page([dict(row, user={'login': 'someone_else'})]))
        self.assertEqual(require_exact_owner_authorization(
            'owner/repo', candidate, tree,
            decision='RC6_624_APPROVE_INTRADAY_SUSPEND_DEPLOY',
            get_page=page([row])), 3)

    def test_host_stress_deferral_needs_distinct_exact_unedited_owner_decision(self):
        candidate, tree = 'c' * 40, 'd' * 40
        default = f'RC6_624_APPROVE_DEPLOY_AND_COLD2 candidate={candidate} tree={tree}'
        deferred = (f'RC6_624_APPROVE_DEPLOY_WITH_HOST_STRESS_DEFERRED '
                    f'candidate={candidate} tree={tree}')
        def row(number, body, *, login='mbalbo2023', updated='t1'):
            return dict(id=number, body=body, user={'login': login},
                        created_at='t1', updated_at=updated)
        def page(rows):
            return lambda _: json.dumps(rows).encode()
        with self.assertRaisesRegex(ValueError, 'EXACT_POSTERIOR_OWNER_AUTHORIZATION_REQUIRED'):
            require_exact_owner_authorization('owner/repo', candidate, tree,
                decision='RC6_624_APPROVE_DEPLOY_WITH_HOST_STRESS_DEFERRED',
                get_page=page([row(1, default)]))
        with self.assertRaisesRegex(ValueError, 'EXACT_POSTERIOR_OWNER_AUTHORIZATION_REQUIRED'):
            require_exact_owner_authorization('owner/repo', candidate, tree,
                decision='RC6_624_APPROVE_DEPLOY_WITH_HOST_STRESS_DEFERRED',
                get_page=page([row(1, deferred, updated='t2')]))
        self.assertEqual(require_exact_owner_authorization(
            'owner/repo', candidate, tree,
            decision='RC6_624_APPROVE_DEPLOY_WITH_HOST_STRESS_DEFERRED',
            get_page=page([row(1, default), row(2, deferred)])), 2)
        with self.assertRaisesRegex(ValueError, 'AUTHORIZATION_DECISION_INVALID'):
            require_exact_owner_authorization('owner/repo', candidate, tree,
                decision='ARBITRARY', get_page=page([]))

    def test_paper_carryover_needs_distinct_exact_owner_decision(self):
        candidate, tree = 'a' * 40, 'b' * 40
        body = (f'RC6_624_APPROVE_INTRADAY_PAPER_CARRYOVER '
                f'candidate={candidate} tree={tree}')
        row = dict(id=4, body=body, user={'login': 'mbalbo2023'},
                   created_at='t1', updated_at='t1')
        page = lambda rows: lambda _: json.dumps(rows).encode()
        self.assertEqual(require_exact_owner_authorization(
            'owner/repo', candidate, tree,
            decision='RC6_624_APPROVE_INTRADAY_PAPER_CARRYOVER',
            get_page=page([row])), 4)
        with self.assertRaisesRegex(ValueError, 'EXACT_POSTERIOR_OWNER_AUTHORIZATION_REQUIRED'):
            require_exact_owner_authorization(
                'owner/repo', candidate, tree,
                decision='RC6_624_APPROVE_INTRADAY_PAPER_CARRYOVER',
                get_page=page([dict(row, updated_at='t2')]))

    def test_postclose_caucion_requires_its_own_exact_unedited_comment(self):
        candidate, tree = 'a' * 40, 'b' * 40
        body = (f'RC6_624_APPROVE_PAUSED_POSTCLOSE_WITH_CAUCION '
                f'candidate={candidate} tree={tree}')
        row = dict(id=5, body=body, user={'login': 'mbalbo2023'},
                   created_at='t1', updated_at='t1')
        page = lambda rows: lambda _: json.dumps(rows).encode()
        self.assertEqual(require_exact_owner_authorization(
            'owner/repo', candidate, tree,
            decision='RC6_624_APPROVE_PAUSED_POSTCLOSE_WITH_CAUCION',
            get_page=page([row])), 5)
        with self.assertRaisesRegex(ValueError, 'EXACT_POSTERIOR_OWNER_AUTHORIZATION_REQUIRED'):
            require_exact_owner_authorization('owner/repo', candidate, tree,
                decision='RC6_624_APPROVE_PAUSED_POSTCLOSE_WITH_CAUCION',
                get_page=page([dict(row, user={'login':'other'})]))
