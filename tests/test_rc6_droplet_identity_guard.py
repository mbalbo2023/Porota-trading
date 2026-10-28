"""Host identity must be exact before any Deploy V2 host mutation."""
from io import BytesIO
from pathlib import Path
import unittest
from unittest.mock import patch

from scripts import rc6_droplet_identity_guard as guard


class Response:
    status = 200

    def __init__(self, body):
        self.body = BytesIO(body)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def read(self, size):
        return self.body.read(size)


class Opener:
    def __init__(self, identity=b'594077619\n', region=b'nyc1\n'):
        self.values = {'id': identity, 'region': region}
        self.requests = []

    def open(self, request, timeout):
        assert timeout == 3
        self.requests.append(request.full_url)
        return Response(self.values[request.full_url.rsplit('/', 1)[-1]])


class IdentityTests(unittest.TestCase):
    def test_exact_id_and_region_with_link_local_metadata(self):
        opener = Opener()
        self.assertEqual(guard.verify('594077619', 'nyc1', opener),
                         ('594077619', 'nyc1'))
        self.assertEqual(opener.requests, [guard.BASE + 'id', guard.BASE + 'region'])

    def test_wrong_host_unavailable_or_oversized_metadata_fails_closed(self):
        for opener in (Opener(identity=b'594077620\n'),
                       Opener(region=b'nyc3\n'),
                       Opener(identity=b'9' * 65),
                       Opener(identity=b'')):
            with self.assertRaises(guard.IdentityRejected):
                guard.verify('594077619', 'nyc1', opener)
        with patch.object(guard.urllib.request, 'build_opener',
                          return_value=Opener(identity=b'594077620')) as factory:
            with self.assertRaisesRegex(guard.IdentityRejected, 'MISMATCH'):
                guard.verify('594077619', 'nyc1')
            factory.assert_called_once()
            self.assertEqual(factory.call_args.args[0].proxies, {})

    def test_workflow_checks_host_before_any_mutation(self):
        workflow = (Path(__file__).resolve().parents[1] /
                    '.github/workflows/porota-deploy-v2-promote.yml').read_text()
        identity = workflow.index('Pin exact DigitalOcean Droplet before host mutation')
        first_mutation = workflow.index('Reconcile only exact A138 cleanly exited host barrier')
        self.assertLess(identity, first_mutation)
        self.assertIn('--expected-id 594077619 --expected-region nyc1', workflow)
        self.assertIn('< scripts/rc6_droplet_identity_guard.py', workflow)


if __name__ == '__main__':
    unittest.main()
