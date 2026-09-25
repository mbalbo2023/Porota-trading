#!/usr/bin/env python3
"""Apply RC6 multi-source PAPER promotion after evidence collection."""
import os, sqlite3
import rc6_contract_promotion as promotion

DB=os.getenv("POROTA_OBSERVER_DB","/opt/porota-trading/data/paper_v17/observer_v17.db")
class Store:
    def connect(self):
        c=sqlite3.connect(DB,timeout=20);c.row_factory=sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON");return c

def main():
    result=promotion.promote(Store())
    print("RC6_CONTRACT_PROMOTION="+__import__("json").dumps(result,sort_keys=True))
    assert result["real_money_authorized"] is False
    print("REAL_ORDERS_SENT=0")
    return 0
if __name__=="__main__": raise SystemExit(main())
