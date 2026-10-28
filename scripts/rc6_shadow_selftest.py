#!/usr/bin/env python3
from pathlib import Path

import es_shadow_binding_contract_rc6 as c


def main():
    rows=c.current_learning_contracts()
    assert len(rows)==4
    assert all(r.stage=='SHADOW' and not r.can_block_paper for r in rows)
    assert all(not r.automatic_promotion for r in rows)
    assert c.contract_snapshot()['real_money_authorized'] is False
    s=c.summarize_counterfactuals([
        {'would_block':True,'realized_net_pnl':-10},
        {'would_block':True,'realized_net_pnl':20},
        {'would_block':False,'realized_net_pnl':5},
        {'would_block':False,'realized_net_pnl':-2},
    ])
    assert s['losses_avoided']==1 and s['gains_removed']==1
    assert s['actual_paper_pnl']==13 and s['counterfactual_pnl_if_gate_bound']==3
    mode=Path('porota_mode_manager.py').read_text(encoding='utf-8')
    obs=Path('bf_production_paper_observer.py').read_text(encoding='utf-8')
    eng=Path('be_paper_engine.py').read_text(encoding='utf-8')
    dash=Path('bg_paper_dashboard.py').read_text(encoding='utf-8')
    checkpoint=Path('POROTA_TRADING_CHECKPOINT_CANONICO_RC6_2026-09-05.md').read_text(encoding='utf-8')
    assert '"PAPER_ECONOMIC_GATE_MODE": "BINDING"' not in mode
    assert mode.count('"PAPER_ECONOMIC_GATE_MODE": "SHADOW"')>=2
    assert 'os.getenv("PAPER_ECONOMIC_GATE_MODE", "BINDING")' not in obs
    assert 'PAPER_ECONOMIC_GATE_MODE = os.getenv("PAPER_ECONOMIC_GATE_MODE", "SHADOW")' in dash
    assert 'features["economics_mode"] = self.economics_mode' in eng
    assert 'economics_mode == "BINDING"' in eng
    assert 'P0-APRENDIZAJE — SHADOW' in checkpoint
    print('RC6_SHADOW_SELFTEST=GREEN')

if __name__=='__main__':
    main()
