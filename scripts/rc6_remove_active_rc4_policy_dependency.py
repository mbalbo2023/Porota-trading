#!/usr/bin/env python3
"""Rewire the active PAPER learning-policy context away from RC4 names."""
from pathlib import Path

P=Path('be_paper_engine.py')


def main():
    text=P.read_text(encoding='utf-8')
    replacements={
      '# RC4: policies are evaluated on every candidate. Observation/SHADOW':'# RC6: learning policies are evaluated on every candidate. Observation/SHADOW',
      'import ck_policy_gate_hf6 as rc4_policy_gate':'import ck_policy_gate_hf6 as policy_gate',
      'import rc4_policy_context':'import es_policy_context_rc6 as policy_context_provider',
      'policy_context = rc4_policy_context.collect(':'policy_context = policy_context_provider.collect(',
      'policy_evaluation = rc4_policy_gate.evaluate(':'policy_evaluation = policy_gate.evaluate(',
      'features["rc4_policy_context_source"]':'features["policy_context_source"]',
      'features["rc4_policy_evaluation"]':'features["policy_evaluation"]',
      'modes = rc4_policy_gate.active_policies()':'modes = policy_gate.active_policies()',
      'features["rc4_policy_error"]':'features["policy_error"]',
      '# RC4 policies was explicitly promoted to BINDING.':'# a learning policy was explicitly promoted to BINDING_PAPER authority.',
    }
    changed=0
    for old,new in replacements.items():
        n=text.count(old)
        if n:
            text=text.replace(old,new)
            changed+=n
        elif new not in text:
            raise SystemExit('RC6_POLICY_REWIRE_ANCHOR_MISSING:'+old[:70])
    # Active source must no longer import or emit RC4 policy identifiers.
    forbidden=('import rc4_policy_context','rc4_policy_gate','rc4_policy_context_source','rc4_policy_evaluation','rc4_policy_error')
    bad=[x for x in forbidden if x in text]
    if bad:
        raise SystemExit('RC6_ACTIVE_RC4_POLICY_REMAINS:'+','.join(bad))
    P.write_text(text,encoding='utf-8')
    print(f'RC6_ACTIVE_POLICY_REWIRE=GREEN|changes={changed}')

if __name__=='__main__':
    main()
