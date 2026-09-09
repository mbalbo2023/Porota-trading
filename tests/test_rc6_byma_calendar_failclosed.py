import ast
import datetime as dt
import sys
import types
import unittest
from pathlib import Path

def load_business_day():
    tree=ast.parse(Path('bf_production_paper_observer.py').read_text(encoding='utf-8'))
    fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_business_day')
    ns={}
    exec(compile(ast.Module(body=[fn],type_ignores=[]),'bf_production_paper_observer.py','exec'),ns)
    return ns['_business_day']

class TestBymaCalendarFailClosed(unittest.TestCase):
    def tearDown(self):
        sys.modules.pop('ak_byma_calendar',None)
    def test_calendar_exception_closes_business_day(self):
        fake=types.ModuleType('ak_byma_calendar')
        def boom(_day): raise RuntimeError('injected calendar failure')
        fake.es_dia_habil_operativo=boom
        sys.modules['ak_byma_calendar']=fake
        self.assertFalse(load_business_day()(dt.date(2026,9,9)))
    def test_calendar_true_is_preserved(self):
        fake=types.ModuleType('ak_byma_calendar')
        fake.es_dia_habil_operativo=lambda _day: True
        sys.modules['ak_byma_calendar']=fake
        self.assertTrue(load_business_day()(dt.date(2026,9,9)))

if __name__=='__main__': unittest.main()
