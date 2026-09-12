import unittest

import rc6_api_contract_crosswalk as x


class TestApiContractCrosswalk(unittest.TestCase):
    def test_exact_ticker_can_bind_but_wildcard_cannot(self):
        identity={'family':'CEDEARS','ticker':'AAPL','market':'BYMA','api_discovered':True}
        records=[
            {'family':'CEDEARS','ticker':'*','market':'UNKNOWN','source_class':'PPI_AUTHENTICATED_WEB'},
            {'family':'CEDEARS','ticker':'AAPL','market':'BYMA','source_class':'PPI_AUTHENTICATED_XHR'},
            {'family':'CEDEARS','ticker':'MSFT','market':'BYMA','source_class':'PPI_AUTHENTICATED_XHR'},
        ]
        out=x.partition(identity,records)
        self.assertEqual(len(out['binding_records']),1)
        self.assertEqual(out['binding_records'][0]['ticker'],'AAPL')
        self.assertEqual(len(out['family_context']),1)
        self.assertFalse(out['wildcard_used_for_binding'])
        self.assertFalse(out['paper_candidate'])

    def test_concrete_market_conflict_rejected(self):
        identity={'family':'ACCIONES-USA','ticker':'AAPL','market':'NYSE','api_discovered':True}
        records=[{'family':'ACCIONES_USA','ticker':'AAPL','market':'NASDAQ'}]
        out=x.partition(identity,records)
        self.assertEqual(out['binding_records'],[])
        self.assertEqual(len(out['rejected']),1)

    def test_unknown_market_on_exact_ticker_is_allowed_as_evidence_not_identity(self):
        identity={'family':'LICITACIONES','ticker':'TECPE10','market':'BYMA','api_discovered':True}
        records=[{'family':'LICITACIONES','ticker':'TECPE10','market':'UNKNOWN'}]
        out=x.partition(identity,records)
        self.assertEqual(len(out['binding_records']),1)

    def test_non_discovered_identity_rejected(self):
        with self.assertRaises(ValueError):
            x.partition({'family':'FCI','ticker':'*','market':'UNKNOWN','api_discovered':False},[])

    def test_no_execution_capability(self):
        x.assert_no_execution_capability()


if __name__=='__main__':
    unittest.main()
