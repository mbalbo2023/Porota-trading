import unittest

import rc6_web_api_candidate_bridge as b


class TestWebApiCandidateBridge(unittest.TestCase):
    def test_fci_name_becomes_candidate_only(self):
        rows=b.extract_candidates(
            family='FCI',
            headers=['NOMBRE','CATEGORÍA','MON.','PLAZO RESC.','HORARIO LÍMITE'],
            rows=[['Adcap Acciones Clase A','Renta variable','ARS','24 hs','14:00']],
            source_route='/Cotizaciones/FCIs',
        )
        self.assertEqual(len(rows),1)
        x=rows[0]
        self.assertEqual(x['name_candidate'],'Adcap Acciones Clase A')
        self.assertIsNone(x['ticker_candidate'])
        self.assertTrue(x['web_candidate_only'])
        self.assertFalse(x['api_discovered'])
        self.assertFalse(x['contract_ready'])
        self.assertFalse(x['paper_candidate'])

    def test_explicit_ticker_is_candidate_not_discovery(self):
        rows=b.extract_candidates(
            family='ETF',headers=['Ticker','Nombre'],rows=[['SPY','SPDR S&P 500 ETF']]
        )
        self.assertEqual(rows[0]['ticker_candidate'],'SPY')
        self.assertFalse(rows[0]['api_discovered'])

    def test_unknown_headers_do_not_infer_identity(self):
        rows=b.extract_candidates(family='FCI',headers=['Dato 1','Dato 2'],rows=[['X','Y']])
        self.assertEqual(rows,[])

    def test_no_execution_capability(self):
        b.assert_no_execution_capability()


if __name__ == '__main__':
    unittest.main()
