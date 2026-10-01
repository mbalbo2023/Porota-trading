from datetime import datetime, timezone

import be_paper_engine
import cf_intraday_scalping as scalping


def test_persists_intraday_unsupported_per_exact_identity_without_readiness_mutation(tmp_path):
    store=be_paper_engine.PaperStore(str(tmp_path/"paper.db"))
    scalping.init_schema(store)
    record={
        "ticker":"BAES","instrument_type":"ACCIONES","market":"BYMA",
        "currency":"ARS","settlement":"A-24HS","capability":"READY_PAPER_SPOT",
        "status":"AVAILABLE",
    }
    at=datetime(2026,10,1,14,0,tzinfo=timezone.utc)
    assert scalping.persist_intraday_unsupported(store,record,checked_at=at)=="PPI_INTRADAY_UNSUPPORTED"
    with store.connect() as c:
        row=c.execute("""SELECT state,detail FROM ppi_intraday_contract_state
          WHERE symbol=? AND asset_class=? AND market=? AND currency=? AND settlement=?""",
          ("BAES","ACCIONES","BYMA","ARS","A-24HS")).fetchone()
    assert row["state"]=="PPI_INTRADAY_UNSUPPORTED"
    assert "MarketData/Intraday" in row["detail"]


def test_intraday_unsupported_is_strategy_endpoint_state_not_generic_capability():
    source=open("cf_intraday_scalping.py",encoding="utf-8").read()
    helper=source.split("def persist_intraday_unsupported",1)[1].split("def persist_payload",1)[0]
    assert "financial_instrument_catalog" not in helper
    assert "candidate_identity_v2" not in helper
    assert "UPDATE " not in helper
    assert "PPI_INTRADAY_UNSUPPORTED" in helper
