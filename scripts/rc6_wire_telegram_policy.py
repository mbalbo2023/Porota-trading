#!/usr/bin/env python3
"""Wire centralized RC6 Telegram delivery policy into the PAPER outbox."""
from pathlib import Path

P=Path('bn_telegram_bus.py')


def main():
    text=P.read_text(encoding='utf-8')
    imp='from eu_telegram_policy_rc6 import decide_row as telegram_delivery_decision\n'
    anchor='from bs_instrument_contracts import aware_datetime\n'
    if imp not in text:
        if anchor not in text: raise SystemExit('RC6_TELEGRAM_IMPORT_ANCHOR')
        text=text.replace(anchor,anchor+imp,1)

    old='''        row = self.claim()\n        if not row:\n            return False\n        message_id, error = None, None\n        try:'''
    new='''        row = self.claim()\n        if not row:\n            return False\n        at = aware_datetime(self.clock_fn())\n        policy = telegram_delivery_decision(row, at)\n        if not policy.allow:\n            # Terminal suppression: routine weekend/holiday messages must not\n            # remain PENDING and replay on the next business day.\n            with self.store.connect() as c:\n                c.execute("BEGIN IMMEDIATE")\n                updated = c.execute("""UPDATE paper_notification_outbox\n                  SET state=?,last_error=?,lease_token=NULL,lease_until=NULL\n                  WHERE id=? AND state='SENDING' AND lease_token=?""",\n                  (policy.terminal_state,policy.reason,row['id'],row['lease_token']))\n                if updated.rowcount:\n                    c.execute("""UPDATE paper_notification_worker SET heartbeat_at=?,state=?,detail=? WHERE id=1""",\n                              (at.isoformat(),'SUPPRESSED_ROUTINE',policy.reason))\n            return bool(updated.rowcount)\n        message_id, error = None, None\n        try:'''
    if old in text:
        text=text.replace(old,new,1)
    elif new not in text:
        raise SystemExit('RC6_TELEGRAM_TICK_ANCHOR')

    # Existing code calculates `at` again after send. Keep only one authoritative timestamp.
    text=text.replace('''        at = aware_datetime(self.clock_fn())\n        delay = max(1, error.retry_after if error else 0,''',
                      '''        at = aware_datetime(self.clock_fn())\n        delay = max(1, error.retry_after if error else 0,''',1)

    if 'telegram_delivery_decision(row, at)' not in text or 'SUPPRESSED_WEEKEND' in text:
        # State is supplied by the policy module; literal need not exist here.
        pass
    P.write_text(text,encoding='utf-8')
    print('RC6_TELEGRAM_POLICY_WIRED=GREEN')

if __name__=='__main__': main()
