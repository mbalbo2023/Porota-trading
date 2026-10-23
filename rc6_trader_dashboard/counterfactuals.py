"""Historical, bounded learning lanes; no live authority or replay execution."""
from datetime import datetime
from decimal import Decimal
import time

from .projection import Page, UNKNOWN, age, decimal_amount, json_object


def profile_decisions(p):
    """Three published profiles per factual decision; no replay on GET."""
    from cg_paper_workspace import artifact_root

    source = "decision_evidence_counterfactual_latest.json · committed SHADOW profiles"
    payload, state = p.bounded_file("reports/decision_evidence_counterfactual_latest.json",
                                    root=artifact_root(p.store.path))
    if state != "AVAILABLE":
        return Page(source, reason="PROFILE_PUBLICATION_" + state)
    generated = _clock(payload.get("generated_at"))
    if (payload.get("compact_projection") is not True or
            payload.get("read_only") is not True or
            payload.get("real_orders_authorized") is not False or
            not generated or generated > p.now or
            not isinstance(payload.get("decisions"), list)):
        return Page(source, reason="PROFILE_PUBLICATION_UNVERIFIED")
    labels = {"BASELINE_CONSERVATIVE_V1": "conservative",
              "SHADOW_BALANCED_V1": "balanced",
              "SHADOW_AGGRESSIVE_V1": "aggressive"}
    rows = []
    for raw in payload["decisions"][:100]:
        if not isinstance(raw, dict) or raw.get("state") != "VERIFIED":
            continue
        stamped = _clock(raw.get("evidence_at"))
        if not stamped or stamped > generated or not raw.get("decision_key"):
            continue
        profiles = raw.get("profiles")
        if not isinstance(profiles, list):
            profiles = []
        row = {"decision_key": raw["decision_key"], "symbol": raw.get("symbol"),
               "factual_decision": raw.get("factual_decision"),
               "as_of": raw["evidence_at"], "entry_authority": False}
        for profile in profiles[:3]:
            if not isinstance(profile, dict) or profile.get("name") not in labels:
                continue
            key = labels[profile["name"]]
            verified = profile.get("state") == "VERIFIED"
            row[key] = profile.get("action") if verified else None
            row[key + "_state"] = "VERIFIED" if verified else "INSUFFICIENT_EVIDENCE"
            row[key + "_reason"] = profile.get("reason")
        for key in labels.values():
            row.setdefault(key + "_state", "NO_VERIFICADO")
        rows.append(row)
    return Page(source, rows[p.offset:p.offset + 10], len(rows),
                "HISTORICAL_OBSERVABLE" if rows else UNKNOWN, p.offset,
                payload["generated_at"],
                "Máximo 100 decisiones publicadas; perfiles SHADOW descriptivos, sin fills ni autoridad de entrada")


def profiles(p):
    from rc6_counterfactual_learning import read_bounded
    result=read_bounded(as_of=p.now,offset=p.offset)
    page=Page(result['source'],result['rows'],result['total'],result['state'],p.offset,
              result.get('as_of'),result['reason']+' · '+result['scope'])
    return page,result['counts']


def _clock(value):
    try:
        at=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        return at if at.tzinfo else None
    except (TypeError,ValueError): return None


def _would_block(detail,key):
    if key=='ECONOMIC_GATE':
        value=json_object(detail.get('economics')).get('passed')
        return not value if type(value) is bool else None
    evaluation=json_object(detail.get('policy_evaluation') or detail.get('rc4_policy_evaluation'))
    gate=json_object(json_object(evaluation.get('gates')).get({
        'EXPECTANCY':'expectancy','MARKET_REGIME':'regime','SECTOR_CONCENTRATION':'sector_concentration'}[key]))
    value=gate.get('would_block')
    return value if type(value) is bool else None


def gates(p):
    s=p.store
    required={'id','evaluated_at','symbol','paper_id','detail_json'}
    if not required<=s.columns('trade_gate_evaluations'):
        return Page('trade_gate_evaluations',reason='GATE_LEDGER_NOT_PUBLISHED')
    before=len(s.errors);deadline=time.monotonic()+.25
    try:
        if s.connection is not None:
            s.connection.set_progress_handler(lambda:int(time.monotonic()>deadline),200)
        rows=s.query('SELECT '+s.select('trade_gate_evaluations')+''' FROM trade_gate_evaluations
            WHERE id>COALESCE((SELECT MAX(id) FROM trade_gate_evaluations),0)-1000
            ORDER BY id DESC LIMIT 100''')
        ids=tuple({row['paper_id'] for row in rows if row.get('paper_id')})
        positions=s.by_keys('paper_positions','paper_id',ids) if ids else []
    finally:
        if s.connection is not None: s.connection.set_progress_handler(None,0)
    if len(s.errors)!=before or time.monotonic()>deadline:
        return Page('trade_gate_evaluations',reason='GATE_SAMPLE_READ_BUDGET_OR_SCHEMA')
    by_id={row['paper_id']:row for row in positions}
    groups={};seen=set()
    for row in rows:
        evaluated=_clock(row.get('evaluated_at'))
        if not evaluated or evaluated>p.now: continue
        position=by_id.get(row.get('paper_id')) or {}
        if position and position.get('symbol')!=row.get('symbol'): continue
        opened,closed=_clock(position.get('opened_at')),_clock(position.get('closed_at'))
        if position and (not opened or evaluated>opened or opened>p.now): continue
        currency=position.get('currency') or UNKNOWN
        net=decimal_amount(position.get('net_pnl')) if position.get('status')=='CLOSED' and closed and opened<=closed<=p.now else None
        if currency not in {'ARS','USD','USD_MEP','USD_CCL'}: net=None
        detail=json_object(row.get('detail_json'))
        for policy in ('ECONOMIC_GATE','EXPECTANCY','MARKET_REGIME','SECTOR_CONCENTRATION'):
            block=_would_block(detail,policy)
            key=(policy,row.get('paper_id') or 'gate:'+str(row['id']))
            if block is None or key in seen: continue
            seen.add(key)
            ident=(policy,currency,position.get('asset_class'),position.get('market'),position.get('settlement'))
            group=groups.setdefault(ident,{'policy':policy,'currency':currency,'family':ident[2],
                'market':ident[3],'settlement':ident[4],'evaluated':0,'would_block':0,'would_allow':0,
                'outcome_closed':0,'outcome_pending':0,'losses_avoided':0,'gains_removed':0,
                '_actual':Decimal(0),'_model':Decimal(0),'as_of':row.get('evaluated_at'),
                'entry_authority':False,'automatic_promotion':False,'economic_edge_validated':False})
            group['evaluated']+=1;group['would_block' if block else 'would_allow']+=1
            if net is None: group['outcome_pending']+=1
            else:
                group['outcome_closed']+=1;group['_actual']+=net
                if not block: group['_model']+=net
                group['losses_avoided']+=int(block and net<0)
                group['gains_removed']+=int(block and net>0)
    result=[]
    for group in groups.values():
        actual, modeled = group.pop('_actual'), group.pop('_model')
        group['actual_paper_pnl']=str(actual) if group['outcome_closed'] else None
        group['counterfactual_if_bound']=str(modeled) if group['outcome_closed'] else None
        group['state']='HISTORICAL_OBSERVABLE'
        result.append(group)
    result.sort(key=lambda row:(row['policy'],row['currency'],str(row['family'])))
    return Page('Latest100 gate records /1000id window → exact paper_id ledger',result[p.offset:p.offset+10],
        len(result) if result else None,'HISTORICAL_OBSERVABLE' if result else UNKNOWN,p.offset,
        rows[0].get('evaluated_at') if rows else None,
        'BOUNDED_SAMPLE; deduplicated per policy/position; closed subsets only; no portfolio feedback')


def exits(p):
    cut=p.shadow_history
    report=cut.get('report') or {}
    if cut.get('state')!='HISTORICAL_COHERENT_SHADOW' or age(report.get('as_of'),p.now) is None:
        return Page('Committed historical economic_exit_lab',reason=cut.get('reason') or 'HISTORICAL_CUT_NOT_PUBLISHED')
    lab=report.get('economic_exit_lab') or {}
    source=lab.get('rows') if isinstance(lab,dict) else None
    if not isinstance(source,list):
        return Page('Committed historical economic_exit_lab',reason='EXIT_LAB_OBSERVATIONS_NOT_PUBLISHED')
    rows=[]
    for raw in source[:100]:
        if not isinstance(raw,dict): continue
        opened,registered=_clock(raw.get('opened_at')),_clock(raw.get('registered_at'))
        if not opened or not registered or not opened<=registered<=_clock(report.get('as_of')): continue
        if any(p.filters.get(k) and raw.get(k)!=p.filters[k] for k in ('currency','family','market','settlement')): continue
        keys=('paper_id','symbol','currency','family','market','settlement','policy','state','modeled_net',
              'opened_at','registered_at','coverage','path_observations','entry_evidence_sha256',
              'path_sha256','comparison_basis')
        row={key:raw.get(key) for key in keys}
        if (row.get('state')!='EVALUABLE' or decimal_amount(row.get('modeled_net')) is None
                or row.get('currency') not in {'ARS','USD','USD_MEP','USD_CCL'}):
            row['reason'] = ('PATH_COVERAGE_NOT_VERIFIED; sampled points do not certify a complete comparable path'
                             if row.get('coverage') != 'MEDIDO' else
                             'MONETARY_IDENTITY_NOT_VERIFIED' if row.get('currency') not in {'ARS','USD','USD_MEP','USD_CCL'} else
                             'COUNTERFACTUAL_NOT_EVALUABLE' if row.get('state') != 'EVALUABLE' else
                             'MODELED_NET_NOT_VERIFIED')
            row.update(modeled_net=None,state='INSUFFICIENT_EVIDENCE')
        row.update(source='committed retrospective SHADOW; same factual entry',as_of=report['as_of'],
                   entry_authority=False,economic_edge_validated=False)
        rows.append(row)
    return Page('CURRENT.json → committed HISTORICAL economic_exit_lab',rows[p.offset:p.offset+10],
        len(rows) if rows else None,'HISTORICAL_OBSERVABLE' if rows else UNKNOWN,p.offset,report.get('as_of'),
        'LATEST_100_POLICY_OBSERVATIONS; model ≠ factual fill; no automatic promotion')
