import copy
import importlib.util
from pathlib import Path
import unittest

SPEC=importlib.util.spec_from_file_location('blocker_analysis',Path(__file__).resolve().parents[1]/'scripts'/'rc6_paper_blockers_analysis.py')
a=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(a)


def fixture():
    row=dict(ticker='ABC',instrument_type='OPCIONES',market='BYMA',currency='ARS',settlement='INMEDIATA',status='PAUSED_EXPLICIT',can_simulate=0,detail='PAUSED_EXPLICIT:OPTION_CONTRACT_UNRESOLVED;CAPABILITY:NEEDS_OPTION_CONTRACT')
    cat={**row,'status':'AVAILABLE','capability':'NEEDS_OPTION_CONTRACT','metadata_json':{'_discovery_source':'PPI_PRIMARY','_contract_bridge':{'status':'BLOCKED','gaps':['MISSING:underlying']}}}
    return dict(schema_version=3,complete=True,product_sha=a.EXPECTED_PRODUCT,observer={'mode':'PRODUCTION_PAPER','real_orders_sent':0},tables={'candidate_identity_v2':[row],'financial_instrument_catalog':[cat],'contract_evidence_v2_current':[]})


class AnalysisTests(unittest.TestCase):
    def test_exhaustive_not_examples(self):
        p=fixture()
        for i in range(1000):
            for table in ('candidate_identity_v2','financial_instrument_catalog'):
                p['tables'][table].append({**p['tables'][table][0],'ticker':f'X{i}'})
        report,m=a.analyze(p)
        self.assertEqual(len(m),1001)
        self.assertEqual(report['blocked'],1001)
        self.assertEqual(sum(c['n'] for c in report['exclusive_causal_cohorts']),1001)
        self.assertEqual(len(report['exclusive_causal_cohorts'][0]['examples']),2)

    def test_no_promotion_no_input_mutation(self):
        p=fixture(); before=copy.deepcopy(p)
        report,m=a.analyze(p)
        self.assertEqual(report['promoted_identities'],0)
        self.assertFalse(m[0]['automatic_promotion'])
        self.assertEqual(p,before)

    def test_literal_ready_is_not_canonical(self):
        p=fixture(); p['tables']['candidate_identity_v2'][0].update(status='READY',can_simulate=1)
        self.assertEqual(a.analyze(p)[0]['ready'],0)

    def test_available_simulation_is_canonical(self):
        p=fixture(); p['tables']['candidate_identity_v2'][0].update(status='AVAILABLE',can_simulate=1,detail='READY_PAPER_OPTION_LONG')
        self.assertEqual(a.analyze(p)[0]['ready'],1)

    def test_incomplete_rejected(self):
        p=fixture();p['complete']=False
        with self.assertRaisesRegex(ValueError,'INCOMPLETE'): a.analyze(p)

    def test_wrong_sha_rejected(self):
        p=fixture();p['product_sha']='0'*40
        with self.assertRaisesRegex(ValueError,'SHA_MISMATCH'): a.analyze(p)

    def test_real_orders_and_unknown_rejected(self):
        for count in [1,None]:
            p=fixture();p['observer']['real_orders_sent']=count
            with self.assertRaisesRegex(ValueError,'SAFETY_MISMATCH'): a.analyze(p)

    def test_duplicate_and_unmatched_keys_rejected(self):
        p=fixture();p['tables']['candidate_identity_v2']*=2
        with self.assertRaisesRegex(ValueError,'DUPLICATE'): a.analyze(p)
        p=fixture();p['tables']['financial_instrument_catalog'][0]['currency']='USD'
        with self.assertRaisesRegex(ValueError,'SCOPE_MISMATCH'): a.analyze(p)

    def test_projected_metadata_width_and_path(self):
        meta=a.metadata({'metadata_json':['PPI_PRIMARY',['MISSING:strike']]},['_discovery_source','_contract_bridge.gaps'])
        self.assertEqual(a.field(meta,'_contract_bridge.gaps'),['MISSING:strike'])
        with self.assertRaisesRegex(ValueError,'WIDTH_MISMATCH'):
            a.metadata({'metadata_json':[1]},['a','b'])

    def test_taxonomy_is_explanatory_and_explicit(self):
        self.assertEqual(a.classify('PPI_PRIMARY_IDENTITY_NOT_VERIFIED'),'E')
        self.assertEqual(a.classify('PPI_FRESHNESS_STALE'),'F')
        self.assertEqual(a.classify('MISSING:underlying'),'A')
        self.assertEqual(a.classify('MISSING:paper_margin_policy'),'B')
        self.assertEqual(a.classify('MISSING:broker_account_permission'),'C')
        self.assertEqual(a.classify('MISSING:fee_schedule'),'D')
        self.assertEqual(a.classify('UNRECOGNIZED_REASON'),'NO_VERIFICADO')


if __name__=='__main__': unittest.main()
