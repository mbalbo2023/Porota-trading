import sqlite3
import unittest

import rc6_api_universe_store as s


class TestApiUniverseStore(unittest.TestCase):
    def setUp(self):
        self.c=sqlite3.connect(':memory:')
        s.init_schema(self.c)

    def tearDown(self):
        self.c.close()

    def test_persists_only_api_discovered_and_never_auto_paper(self):
        s.upsert_discovered(self.c,{
            'family':'ACCIONES-USA','ticker':'AAPL','market':'NYSE',
            'description':'Apple','currency':'Dolares divisa | CCL',
            'api_discovered':True,'context_only':False,'paper_candidate':False,
        })
        rows=s.rows(self.c)
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['ticker'],'AAPL')
        self.assertEqual(rows[0]['api_discovered'],1)
        self.assertEqual(rows[0]['paper_candidate'],0)
        self.assertEqual(rows[0]['contract_ready'],0)
        self.assertEqual(rows[0]['history_ready'],0)
        self.assertEqual(rows[0]['simulator_ready'],0)

    def test_web_candidate_is_rejected(self):
        with self.assertRaises(ValueError):
            s.upsert_discovered(self.c,{
                'family':'FCI','ticker':'*','market':'UNKNOWN','api_discovered':False,
                'context_only':False,'paper_candidate':False,
            })

    def test_context_only_is_rejected(self):
        with self.assertRaises(ValueError):
            s.upsert_discovered(self.c,{
                'family':'INDICES','ticker':'MERVAL','market':'BYMA','api_discovered':True,
                'context_only':True,'paper_candidate':False,
            })

    def test_auto_paper_is_rejected(self):
        with self.assertRaises(ValueError):
            s.upsert_discovered(self.c,{
                'family':'ETF','ticker':'SPY','market':'NYSE','api_discovered':True,
                'context_only':False,'paper_candidate':True,
            })

    def test_no_execution_capability(self):
        s.assert_no_execution_capability()


if __name__ == '__main__':
    unittest.main()
