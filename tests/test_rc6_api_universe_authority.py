import unittest

import rc6_api_universe_authority as u


API_TYPES = [
    'ACCIONES','ACCIONES-USA','BONOS','CAUCIONES','CEDEARS','ETF','FCI',
    'FCI-EXTERIOR','FUTUROS','LEBAC','LETRAS','LICITACIONES','NOBAC','ON','OPCIONES'
]
API_MARKETS = ['BYMA','NASDAQ','NYSE','OTC','ROFEX']


class TestApiUniverseAuthority(unittest.TestCase):
    def test_web_only_context_never_enters_binding(self):
        for family in ('INDICES','MONEDAS','TASAS'):
            x=u.classify_identity(family=family,ticker='CTX',market='BYMA',api_types=API_TYPES)
            self.assertEqual(x['status'],'CONTEXT_ONLY')
            self.assertTrue(x['context_only'])
            self.assertFalse(u.eligible_for_contract_binding(x))
            self.assertFalse(x['paper_candidate'])

    def test_api_declared_requires_concrete_identity(self):
        x=u.classify_identity(family='FCI_LOCAL',ticker='*',market='UNKNOWN',api_types=API_TYPES)
        self.assertEqual(x['family'],'FCI')
        self.assertEqual(x['status'],'API_DECLARED_NOT_DISCOVERED')
        self.assertFalse(u.eligible_for_contract_binding(x))

    def test_api_discovered_is_binding_eligible_but_never_auto_paper(self):
        x=u.classify_identity(family='ACCIONES_USA',ticker='MSFT',market='NASDAQ',api_types=API_TYPES)
        self.assertEqual(x['family'],'ACCIONES-USA')
        self.assertEqual(x['status'],'API_DISCOVERED')
        self.assertTrue(u.eligible_for_contract_binding(x))
        self.assertFalse(x['paper_candidate'])

    def test_discovery_query_requires_explicit_market_name_and_ticker(self):
        q=u.DiscoveryQuery('ETF','SPY','SPY','NYSE')
        self.assertEqual((q.family,q.ticker,q.name,q.market),('ETF','SPY','SPY','NYSE'))
        with self.assertRaises(ValueError):
            u.DiscoveryQuery('ETF','SPY','','NYSE')

    def test_fuzzy_results_validate_returned_identity_not_seed(self):
        q=u.DiscoveryQuery('ETF','QQQ','QQQ','NYSE')
        rows=[
            {'ticker':'CQQQ','description':'China ETF','currency':'USD','type':'ETF','market':'NYSE'},
            {'ticker':'SQQQ','description':'Short QQQ','currency':'USD','type':'ETF','market':'NYSE'},
            {'ticker':'TQQQ','description':'Ultra QQQ','currency':'USD','type':'ETF','market':'NYSE'},
        ]
        valid=u.validate_search_results(q,rows,api_types=API_TYPES,api_markets=API_MARKETS)
        self.assertEqual([x['ticker'] for x in valid],['CQQQ','SQQQ','TQQQ'])
        self.assertTrue(all(x['api_discovered'] for x in valid))
        self.assertTrue(all(not x['exact_ticker_match'] for x in valid))
        self.assertNotIn('QQQ',[x['ticker'] for x in valid])
        self.assertTrue(all(not x['paper_candidate'] for x in valid))

    def test_search_result_wrong_market_or_family_is_rejected(self):
        q=u.DiscoveryQuery('ETF','SPY','SPY','NYSE')
        rows=[
            {'ticker':'SPY','type':'ETF','market':'NASDAQ'},
            {'ticker':'SPY','type':'ACCIONES-USA','market':'NYSE'},
        ]
        self.assertEqual(u.validate_search_results(q,rows,api_types=API_TYPES,api_markets=API_MARKETS),[])

    def test_module_has_no_execution_capability(self):
        u.assert_no_execution_capability()


if __name__ == '__main__':
    unittest.main()
