"""RC6 #624: co-frescura nativa con relojes UTC y monotónico coherentes.

Contrato B: co-frescura en la observación 2, por parejas adyacentes.
Los relojes UTC y monotónico avanzan exactamente igual en el escenario.
El caso vencido falla contra fb83d387 antes de la corrección.
"""

from datetime import timedelta
import json

import pytest

from scripts import rc6_deploy_generation_gate as gate
from tests.test_rc6_deploy_generation_gate import AT, HEALTH, SHA, TREE, cut


@pytest.mark.parametrize(
    ("initial_observation_offset", "expected"),
    [
        (117.0, "GREEN"),   # primera edad en o2 = 119 s
        (118.0, "GREEN"),   # borde inclusivo: 120 s
        (118.001, "RED"),   # supera el presupuesto por 1 ms
        (119.0, "RED"),     # primera edad en o2 = 121 s
    ],
)
def test_native_two_generations_cofresh_at_second_observation(
    tmp_path, initial_observation_offset, expected
):
    """El segundo cut puede ser fresco sin que el primero siga siéndolo."""
    (tmp_path / "runtime-health.json").write_text(json.dumps(HEALTH))
    elapsed = [0.0]
    reads = [0]

    def reader(*_args, **_kwargs):
        reads[0] += 1
        value = cut()
        # Min(120, 2*(30+45)) = 120 para ambos cuts.
        value["status"]["generation_timing"]["tick_seconds"] = 45
        if reads[0] >= 2:
            value["pointer"]["sequence"] = 2
            value["pointer"]["generation_id"] = "4" * 64
            # El source 2 es 80 s posterior al primero; a o2 aún es fresco.
            value["status"]["as_of"] = (AT + timedelta(seconds=80)).isoformat()
        return value

    def monotonic():
        return elapsed[0]

    def now():
        # Avanza UTC a la misma velocidad que monotonic y wait(2).
        return AT + timedelta(seconds=initial_observation_offset + elapsed[0])

    def wait(seconds):
        elapsed[0] += seconds

    result = gate.collect(
        tmp_path / "dynamic-shadow", SHA, TREE, timeout=5,
        clock=monotonic, wait=wait, now=now, reader=reader,
    )
    assert reads[0] >= 2
    assert result["status"] == expected, (
        f"Primera edad en o2 = {initial_observation_offset + 2:.3f}s; "
        f"resultado observado={result['status']}, esperado={expected}"
    )


@pytest.mark.parametrize('initial_offset,expected', [
    (68.0, 'GREEN'), (68.001, 'RED'),
])
def test_first_cut_keeps_its_own_shorter_measured_budget(tmp_path, initial_offset, expected):
    (tmp_path/'runtime-health.json').write_text(json.dumps(HEALTH))
    elapsed=[0.0];reads=[0]
    def reader(*_args, **_kwargs):
        reads[0]+=1
        value=cut()
        # B1=2*(30+5)=70, while B2=120. The second must not renew B1.
        value['status']['generation_timing']['tick_seconds']=5 if reads[0]==1 else 45
        if reads[0]>=2:
            value['pointer'].update(sequence=2,generation_id='4'*64)
            value['status']['as_of']=(AT+timedelta(seconds=50)).isoformat()
        return value
    result=gate.collect(tmp_path/'dynamic-shadow',SHA,TREE,timeout=5,
        clock=lambda:elapsed[0],wait=lambda s:elapsed.__setitem__(0,elapsed[0]+s),
        now=lambda:AT+timedelta(seconds=initial_offset+elapsed[0]),reader=reader)
    assert result['status']==expected
    if expected=='RED':
        assert result['rejection_count']>=1
        assert any(o.get('reason')=='GENERATION_PREVIOUS_STALE_OR_FUTURE'
                   and o.get('previous_age_seconds')==round(initial_offset+2,6)
                   and o.get('previous_budget_seconds')==70
                   for o in result['observations'])


def test_strict_ten_rejects_last_adjacent_pair_with_expired_predecessor(tmp_path):
    (tmp_path/'runtime-health.json').write_text(json.dumps(HEALTH))
    elapsed=[0.0];reads=[0]
    def reader(*_args, **_kwargs):
        index=reads[0];reads[0]+=1
        value=cut()
        value['pointer'].update(sequence=index+1,generation_id=f'{index+1:064x}')
        value['status']['as_of']=(AT+timedelta(seconds=index)).isoformat()
        value['status']['generation_timing']['tick_seconds']=5  # B=70
        return value
    result=gate.collect(tmp_path/'dynamic-shadow',SHA,TREE,timeout=25,
        clock=lambda:elapsed[0],wait=lambda s:elapsed.__setitem__(0,elapsed[0]+s),
        now=lambda:AT+timedelta(seconds=61+elapsed[0]),reader=reader,
        minimum_generations=10,strict=True)
    assert reads[0]==10
    assert result['status']=='RED' and result['strict'] is True
    assert [g['sequence'] for g in result['generations']]==list(range(1,10))
    assert result['error_reason']=='GENERATION_PREVIOUS_STALE_OR_FUTURE'
    assert result['observations'][-1]['previous_age_seconds']==71
    assert result['observations'][-1]['previous_budget_seconds']==70


def test_utc_jump_cannot_preserve_previously_fresh_cut(tmp_path):
    (tmp_path/'runtime-health.json').write_text(json.dumps(HEALTH))
    elapsed=[0.0];reads=[0]
    def reader(*_args, **_kwargs):
        reads[0]+=1
        value=cut()
        value['status']['generation_timing']['tick_seconds']=45
        if reads[0]>=2:
            value['pointer'].update(sequence=2,generation_id='4'*64)
            value['status']['as_of']=(AT+timedelta(seconds=80)).isoformat()
        return value
    result=gate.collect(tmp_path/'dynamic-shadow',SHA,TREE,timeout=5,
        clock=lambda:elapsed[0],wait=lambda s:elapsed.__setitem__(0,elapsed[0]+s),
        now=lambda:AT+timedelta(seconds=1 if reads[0]==1 else 121),reader=reader)
    assert result['status']=='RED'
    assert any(o.get('reason')=='GENERATION_PREVIOUS_STALE_OR_FUTURE'
               for o in result['observations'])


def test_strict_ten_accepts_adjacent_pairs_after_oldest_cut_expires(tmp_path):
    (tmp_path / 'runtime-health.json').write_text(json.dumps(HEALTH))
    elapsed = [0.0]

    def reader(*_args, **_kwargs):
        index = min(9, int(elapsed[0] // 30))
        value = cut()
        value['pointer'].update(sequence=index + 1, generation_id=f'{index + 1:064x}')
        value['status']['as_of'] = (AT + timedelta(seconds=30 * index)).isoformat()
        value['status']['generation_timing']['tick_seconds'] = 5  # budget 70
        return value

    result = gate.collect(
        tmp_path / 'dynamic-shadow', SHA, TREE, timeout=275,
        clock=lambda: elapsed[0],
        wait=lambda seconds: elapsed.__setitem__(0, elapsed[0] + seconds),
        now=lambda: AT + timedelta(seconds=10 + elapsed[0]),
        reader=reader, minimum_generations=10, strict=True,
    )
    assert result['status'] == 'GREEN'
    assert [g['sequence'] for g in result['generations']] == list(range(1, 11))
    assert result['rejection_count'] == 0
    assert 10 + elapsed[0] == 280  # first age at final observation > its budget
    assert result['observations'][-1]['previous_age_seconds'] == 40


def test_extreme_utc_jump_rejects_without_unbounded_age_receipt(tmp_path):
    (tmp_path / 'runtime-health.json').write_text(json.dumps(HEALTH))
    elapsed = [0.0]
    reads = [0]

    def reader(*_args, **_kwargs):
        reads[0] += 1
        value = cut()
        value['status']['generation_timing']['tick_seconds'] = 45
        if reads[0] >= 2:
            value['pointer'].update(sequence=2, generation_id='4' * 64)
            value['status']['as_of'] = (AT + timedelta(days=2)).isoformat()
        return value

    result = gate.collect(
        tmp_path / 'dynamic-shadow', SHA, TREE, timeout=5,
        clock=lambda: elapsed[0],
        wait=lambda seconds: elapsed.__setitem__(0, elapsed[0] + seconds),
        now=lambda: AT + timedelta(seconds=1) if reads[0] == 1 else AT + timedelta(days=2, seconds=40),
        reader=reader,
    )
    assert result['status'] == 'RED'
    rejected = [o for o in result['observations'] if o['kind'] == 'REJECTED']
    assert rejected and rejected[0]['reason'] == 'GENERATION_PREVIOUS_STALE_OR_FUTURE'
    assert 'previous_age_seconds' not in rejected[0]
    assert rejected[0]['previous_budget_seconds'] == 120
