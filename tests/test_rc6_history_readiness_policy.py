import unittest

import rc6_history_readiness_policy as h


class TestHistoryReadinessPolicy(unittest.TestCase):
    def test_strategy_floor_is_30_or_warmup(self):
        self.assertEqual(h.required_bars_for_warmup(5), 30)
        self.assertEqual(h.required_bars_for_warmup(60), 60)
        with self.assertRaises(ValueError):
            h.required_bars_for_warmup(-1)

    def test_29_bars_is_not_ready(self):
        out=h.evaluate_identity_history(bar_count=29,required_bars=30,exact_identity=True,market_compatible=True,freshness_ok=True)
        self.assertFalse(out['history_ready'])
        self.assertIn('INSUFFICIENT_BARS',out['reasons'])
        self.assertFalse(out['paper_candidate'])

    def test_freshness_must_be_explicitly_proven(self):
        out=h.evaluate_identity_history(bar_count=200,required_bars=30,exact_identity=True,market_compatible=True,freshness_ok=False)
        self.assertFalse(out['history_ready'])
        self.assertIn('FRESHNESS_NOT_PROVEN',out['reasons'])

    def test_identity_and_market_are_binding_prerequisites(self):
        out=h.evaluate_identity_history(bar_count=200,required_bars=30,exact_identity=False,market_compatible=False,freshness_ok=True)
        self.assertFalse(out['history_ready'])
        self.assertIn('IDENTITY_NOT_EXACT',out['reasons'])
        self.assertIn('MARKET_NOT_COMPATIBLE',out['reasons'])

    def test_ready_still_never_promotes_paper(self):
        out=h.evaluate_identity_history(bar_count=30,required_bars=30,exact_identity=True,market_compatible=True,freshness_ok=True)
        self.assertTrue(out['history_ready'])
        self.assertFalse(out['paper_candidate'])

    def test_required_bars_cannot_weaken_consumer_floor(self):
        with self.assertRaises(ValueError):
            h.evaluate_identity_history(bar_count=100,required_bars=5,exact_identity=True,market_compatible=True,freshness_ok=True)

    def test_module_has_no_execution_capability(self):
        h.assert_no_execution_capability()


if __name__ == '__main__':
    unittest.main()
