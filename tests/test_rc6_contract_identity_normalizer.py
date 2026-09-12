import unittest

import rc6_contract_identity_normalizer as n


class TestContractIdentityNormalizer(unittest.TestCase):
    def test_futures_whitespace_only_formatting_maps_to_existing_api_identity(self):
        api = ['DLR/SEP26', 'DLR/SEP26/OCT26 M', 'DLR/OCT26A']
        self.assertEqual(n.unique_api_match(family='FUTUROS', web_identity='DLR / SEP26', api_tickers=api), 'DLR/SEP26')
        self.assertEqual(n.unique_api_match(family='FUTUROS', web_identity='DLR / SEP26 / OCT26 M', api_tickers=api), 'DLR/SEP26/OCT26 M')

    def test_futures_collision_fails_closed(self):
        api = ['DLR/SEP26 M', 'DLR/SEP26M']
        self.assertIsNone(n.unique_api_match(family='FUTUROS', web_identity='DLR / SEP26 M', api_tickers=api))

    def test_caucion_requires_explicit_currency_days_and_existing_api_identity(self):
        api = ['PESOS1', 'PESOS2', 'PESOS7', 'PESOS30', 'DOLAR1']
        self.assertEqual(n.unique_api_match(family='CAUCIONES', web_identity='1 Pesos 1d', api_tickers=api), 'PESOS1')
        self.assertEqual(n.unique_api_match(family='CAUCIONES', web_identity='1 Dólares 1d', api_tickers=api), 'DOLAR1')
        self.assertIsNone(n.unique_api_match(family='CAUCIONES', web_identity='3 Pesos 3d', api_tickers=api))
        self.assertIsNone(n.unique_api_match(family='CAUCIONES', web_identity='Pesos 1d', api_tickers=api))

    def test_options_and_unproven_families_remain_fail_closed(self):
        self.assertIsNone(n.unique_api_match(family='OPCIONES', web_identity='ALU C1400.O', api_tickers=['ALUC1400O']))
        self.assertIsNone(n.unique_api_match(family='BONOS', web_identity='AL30', api_tickers=['AL30']))

    def test_module_has_no_execution_capability(self):
        n.assert_no_execution_capability()


if __name__ == '__main__':
    unittest.main()
