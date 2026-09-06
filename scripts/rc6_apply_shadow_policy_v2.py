#!/usr/bin/env python3
"""Second-stage SHADOW migration: runtime defaults and gate audit semantics."""
from pathlib import Path


def replace(path, old, new, expected=1):
    p=Path(path); text=p.read_text(encoding='utf-8'); n=text.count(old)
    if n==0 and new in text:
        return
    if n!=expected:
        raise SystemExit(f'RC6_SHADOW_V2_ANCHOR:{path}:{n}:expected={expected}')
    p.write_text(text.replace(old,new),encoding='utf-8')


def main():
    # Re-run stage 1 idempotently.
    import rc6_apply_shadow_policy_and_checkpoint as v1
    v1.main()

    # Observer health must not silently assume BINDING when env is absent.
    replace('bf_production_paper_observer.py',
            'os.getenv("PAPER_ECONOMIC_GATE_MODE", "BINDING").upper() == "BINDING"',
            'os.getenv("PAPER_ECONOMIC_GATE_MODE", "SHADOW").upper() == "BINDING"')

    # Persist the authority used for every economic counterfactual.  This lets
    # record_gates distinguish an expected SHADOW open from an invariant breach.
    replace('be_paper_engine.py',
            '            features["economics"] = economics\n            if not economics["passed"]:',
            '            features["economics"] = economics\n            features["economics_mode"] = self.economics_mode\n            if not economics["passed"]:')

    old='''        economics = detail.get("economics") if isinstance(detail, dict) else None\n        contradiction = (final == "OPENED_SIMULATED" and\n            (technical != "APPROVE" or patrimonial != "APPROVE" or\n             not isinstance(economics, dict) or economics.get("passed") is not True))'''
    new='''        economics = detail.get("economics") if isinstance(detail, dict) else None\n        economics_mode = str(detail.get("economics_mode") or "BINDING").upper() if isinstance(detail, dict) else "BINDING"\n        economic_violation = (economics_mode == "BINDING" and\n                              (not isinstance(economics, dict) or economics.get("passed") is not True))\n        contradiction = (final == "OPENED_SIMULATED" and\n            (technical != "APPROVE" or patrimonial != "APPROVE" or economic_violation))'''
    replace('be_paper_engine.py',old,new)

    # Pure truth text should explain the current SHADOW campaign first.
    replace('eo_dashboard_truth_semantics_rc6.py',
            '        return f"{key} = sólo observación; no bloquea por esta política."',
            '        return f"{key} = aprendizaje contrafactual; calcula would_allow/would_block y NO veta PAPER."')

    # Active policy-gate module documentation must not describe itself as RC4.
    replace('ck_policy_gate_hf6.py',
            '"""RC4 policy gates: evaluate always; only BINDING has blocking authority.',
            '"""Policy gates: evaluate always; only an explicitly promoted BINDING policy has blocking authority.')

    # Static safety audit: no runtime default may silently choose BINDING for
    # the economic learning gate. Comparisons with BINDING remain legitimate.
    bad=[]
    for p in Path('.').glob('*.py'):
        text=p.read_text(encoding='utf-8',errors='replace')
        if 'os.getenv("PAPER_ECONOMIC_GATE_MODE", "BINDING")' in text:
            bad.append(str(p))
        if '"PAPER_ECONOMIC_GATE_MODE": "BINDING"' in text:
            bad.append(str(p))
    if bad:
        raise SystemExit('RC6_SHADOW_BINDING_DEFAULT_REMAINS:'+','.join(sorted(set(bad))))
    print('RC6_SHADOW_RUNTIME_SEMANTICS=GREEN')

if __name__=='__main__':
    main()
