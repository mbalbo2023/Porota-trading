#!/usr/bin/env python3
"""Apply small, isolated, pre-Monday audit fixes for RC6.

- RC5-06 missing startup/order gate -> fail closed.
- RC5-08 weekend/holiday closing summary -> suppress routine closing notice.
- RC5-04 remove meaningless Persistent= from monotonic-only timers.

No trading parameters are changed here.
"""
from pathlib import Path


def patch_order_gate():
    p=Path('c_ppi_client.py'); text=p.read_text(encoding='utf-8')
    old='''            except ImportError:\n                pass  # sin el portón instalado, el comportamiento es el de siempre'''
    new='''            except ImportError as exc:\n                # Safety control missing at the only real-order choke point must fail closed.\n                raise RuntimeError("ORDER_GATE_MISSING: ao_startup_gate no disponible") from exc'''
    if old in text:
        text=text.replace(old,new,1)
    elif new not in text:
        raise SystemExit('RC6_AUDIT_ORDER_GATE_ANCHOR_MISSING')
    p.write_text(text,encoding='utf-8')


def patch_weekend_closing():
    p=Path('bi_operational_services.py'); text=p.read_text(encoding='utf-8')
    old='''    hour = datetime.now(TZ).hour\n    if phase == "CLOSED" and hour >= int(os.getenv("MARKET_CLOSE_HOUR", "17")):\n        if force or _job_due(store, "REPORTS", 6 * 3600):\n            ensure_reports(store, include_today=True)\n        send_closing_summary(store)'''
    new='''    now = datetime.now(TZ)\n    hour = now.hour\n    # Routine close summaries only belong to an actual BYMA business day.\n    # Critical infrastructure alerts use their own paths and are never suppressed here.\n    try:\n        import ak_byma_calendar as byma_calendar\n        business_day = bool(byma_calendar.es_dia_habil_operativo(now.date()))\n    except Exception:\n        business_day = False  # fail closed for routine weekend/holiday messaging\n    if phase == "CLOSED" and business_day and hour >= int(os.getenv("MARKET_CLOSE_HOUR", "17")):\n        if force or _job_due(store, "REPORTS", 6 * 3600):\n            ensure_reports(store, include_today=True)\n        send_closing_summary(store)'''
    if old in text:
        text=text.replace(old,new,1)
    elif new not in text:
        raise SystemExit('RC6_AUDIT_WEEKEND_CLOSE_ANCHOR_MISSING')
    p.write_text(text,encoding='utf-8')


def patch_timers():
    changed=[]
    for root in (Path('systemd'),Path('deploy/systemd')):
        if not root.exists(): continue
        for p in sorted(root.glob('*.timer')):
            text=p.read_text(encoding='utf-8')
            if 'Persistent=' not in text:
                continue
            is_calendar='OnCalendar=' in text
            is_monotonic=('OnBootSec=' in text or 'OnUnitActiveSec=' in text or 'OnActiveSec=' in text)
            if is_calendar:
                continue
            if not is_monotonic:
                raise SystemExit(f'RC6_TIMER_PERSISTENT_UNKNOWN_SEMANTICS:{p}')
            lines=[ln for ln in text.splitlines() if not ln.strip().startswith('Persistent=')]
            p.write_text('\n'.join(lines)+'\n',encoding='utf-8')
            changed.append(str(p))
    print('RC6_MONOTONIC_PERSISTENT_REMOVED='+str(len(changed)))
    for p in changed: print('TIMER_FIXED='+p)


def verify():
    order=Path('c_ppi_client.py').read_text(encoding='utf-8')
    assert 'ORDER_GATE_MISSING' in order
    assert 'except ImportError:\n                pass' not in order
    svc=Path('bi_operational_services.py').read_text(encoding='utf-8')
    assert 'business_day and hour >=' in svc
    assert 'business_day = False  # fail closed' in svc
    for root in (Path('systemd'),Path('deploy/systemd')):
        if not root.exists(): continue
        for p in root.glob('*.timer'):
            t=p.read_text(encoding='utf-8')
            if 'Persistent=' in t and 'OnCalendar=' not in t and any(x in t for x in ('OnBootSec=','OnUnitActiveSec=','OnActiveSec=')):
                raise AssertionError(f'Persistent remains on monotonic timer: {p}')


def main():
    patch_order_gate(); patch_weekend_closing(); patch_timers(); verify()
    print('RC6_AUDIT_P0_SMALL_FIXES=GREEN')

if __name__=='__main__': main()
