"""RC6 Forward Lab v2 temporal-validation primitives.

Offline/read-only only. This module does not train strategies, access brokers,
open orders, write databases, or authorize promotion. It provides reproducible
walk-forward folds, leave-one-symbol-out cohorts and trading-day block bootstrap
indices without random train/test shuffling or look-ahead.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import date, datetime
import hashlib
import json
import random
from typing import Iterable


@dataclass(frozen=True)
class Fold:
    train_days: tuple[str, ...]
    embargo_days: tuple[str, ...]
    test_days: tuple[str, ...]


def _day(value) -> str:
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value)
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except Exception as exc:
        raise ValueError('TRADING_DAY_INVALID') from exc


def ordered_unique_days(days: Iterable) -> list[str]:
    values=[_day(x) for x in days]
    if len(values)!=len(set(values)):
        raise ValueError('TRADING_DAY_DUPLICATED')
    if values!=sorted(values):
        raise ValueError('TRADING_DAYS_NOT_CHRONOLOGICAL')
    return values


def expanding_walk_forward(days: Iterable, *, min_train_days: int, test_days: int,
                           embargo_days: int = 1, step_days: int | None = None) -> list[Fold]:
    """Create expanding-window temporal folds with an explicit day embargo."""
    values=ordered_unique_days(days)
    for name,value in [('min_train_days',min_train_days),('test_days',test_days),('embargo_days',embargo_days)]:
        if not isinstance(value,int) or isinstance(value,bool) or value < (0 if name=='embargo_days' else 1):
            raise ValueError(name.upper()+'_INVALID')
    step=test_days if step_days is None else step_days
    if not isinstance(step,int) or isinstance(step,bool) or step<1:
        raise ValueError('STEP_DAYS_INVALID')
    folds=[]; train_end=min_train_days
    while True:
        embargo_end=train_end+embargo_days
        test_end=embargo_end+test_days
        if test_end>len(values): break
        folds.append(Fold(tuple(values[:train_end]),tuple(values[train_end:embargo_end]),tuple(values[embargo_end:test_end])))
        train_end += step
    return folds


def leave_one_symbol_out(rows: Iterable[dict]) -> list[dict]:
    rows=list(rows)
    symbols=sorted({str(r.get('symbol') or '').strip().upper() for r in rows if str(r.get('symbol') or '').strip()})
    if len(symbols)<2:
        raise ValueError('NEEDS_AT_LEAST_TWO_SYMBOLS')
    cohorts=[]
    for symbol in symbols:
        train=[r for r in rows if str(r.get('symbol') or '').strip().upper()!=symbol]
        test=[r for r in rows if str(r.get('symbol') or '').strip().upper()==symbol]
        cohorts.append({'held_out_symbol':symbol,'train':train,'test':test,'promotion_allowed':False})
    return cohorts


def validate_temporal_samples(rows: Iterable[dict]) -> dict:
    rows=list(rows); seen=set(); versions=set()
    for row in rows:
        sid=str(row.get('sample_id') or '')
        if not sid or sid in seen: raise ValueError('SAMPLE_ID_MISSING_OR_DUPLICATE')
        seen.add(sid)
        version=str(row.get('strategy_version') or '')
        if not version: raise ValueError('STRATEGY_VERSION_MISSING')
        versions.add(version)
        fa=str(row.get('features_available_at') or '')
        decision=str(row.get('decision_at') or '')
        label=str(row.get('label_available_at') or '')
        try:
            f=datetime.fromisoformat(fa.replace('Z','+00:00'))
            d=datetime.fromisoformat(decision.replace('Z','+00:00'))
            l=datetime.fromisoformat(label.replace('Z','+00:00'))
        except Exception as exc:
            raise ValueError('SAMPLE_TIMESTAMP_INVALID') from exc
        if any(x.tzinfo is None or x.utcoffset() is None for x in (f,d,l)):
            raise ValueError('SAMPLE_TIMESTAMP_NAIVE')
        if not f<=d<=l:
            raise ValueError('LOOKAHEAD_OR_LABEL_ORDER_INVALID')
    if len(versions)>1:
        raise ValueError('MIXED_STRATEGY_VERSIONS')
    return {'samples':len(rows),'strategy_version':next(iter(versions),None),'lookahead_free':True,'promotion_allowed':False}


def trading_day_block_bootstrap(days: Iterable, *, block_length: int, replications: int,
                                seed: int) -> list[list[str]]:
    """Deterministic fixed-block bootstrap over ordered trading days.

    Blocks are contiguous in trading-day order; no observation-level iid shuffle.
    """
    values=ordered_unique_days(days)
    if not values: raise ValueError('TRADING_DAYS_EMPTY')
    if not isinstance(block_length,int) or block_length<1 or block_length>len(values):
        raise ValueError('BLOCK_LENGTH_INVALID')
    if not isinstance(replications,int) or replications<1:
        raise ValueError('REPLICATIONS_INVALID')
    rng=random.Random(int(seed)); starts=list(range(0,len(values)-block_length+1)); out=[]
    for _ in range(replications):
        sample=[]
        while len(sample)<len(values):
            start=rng.choice(starts)
            sample.extend(values[start:start+block_length])
        out.append(sample[:len(values)])
    return out


def manifest(*, strategy_version: str, dataset_identity: dict, folds: list[Fold], parameters: dict) -> dict:
    if not strategy_version: raise ValueError('STRATEGY_VERSION_MISSING')
    payload={'schema':'POROTA_FORWARD_LAB_V2_RC6','strategy_version':strategy_version,
             'dataset_identity':dataset_identity,'folds':[asdict(x) for x in folds],
             'parameters':parameters,'promotion_allowed':False,'auto_promotion':False}
    canonical=json.dumps(payload,sort_keys=True,separators=(',',':'),ensure_ascii=False)
    payload['manifest_sha256']=hashlib.sha256(canonical.encode('utf-8')).hexdigest()
    return payload


def assert_offline_only():
    assert expanding_walk_forward
    assert leave_one_symbol_out
    assert trading_day_block_bootstrap
