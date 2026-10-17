"""Shared evidence bounds and complete option identity authority stay exact."""
import gzip
import hashlib
import json

import pytest

from rc6_shadow_runtime.persistence import EvidenceFiles
from rc6_shadow_runtime import families
from tests.test_rc6_shadow_family_runtime import AT, clocked, fixture_db, instrument, snapshot, source_row


def test_shared_nested_bounds_are_fresh_for_each_atomic_encoding(tmp_path):
    shared={"origins":[{"source":"PPI","note":"ñ🚀"}],"value":None}
    payload={"rows":[{"n":n,"evidence":shared} for n in range(700)]}
    files=EvidenceFiles(tmp_path/"shadow")
    with files:
        for value in (None,"changed"+"ñ🚀"*4000):
            if value is not None:
                payload["rows"]=payload["rows"][:5]
            shared["value"]=value
            expected=json.dumps(payload,sort_keys=True,separators=(",",":"),allow_nan=False).encode()
            receipt=files.write_streamed_report(payload)
            actual=gzip.decompress((files.root/receipt["file"]).read_bytes())
            assert actual==expected
            assert receipt["sha256"]==hashlib.sha256(expected).hexdigest()


def test_shared_cycle_never_replaces_prior_atomic_evidence(tmp_path):
    files=EvidenceFiles(tmp_path/"shadow")
    shared={"value":[]}
    payload={"rows":[shared]*700}
    with files:
        files.write("status.json",{"status":"prior"})
        before=(files.root/"status.json").read_bytes()
        shared["value"].append(shared)
        with pytest.raises(ValueError):
            files.write("status.json",payload)
        assert (files.root/"status.json").read_bytes()==before
        assert not list(files.root.glob("*.tmp"))


@pytest.mark.parametrize("underlyings,selected",[
    ([("ACCIONES","INMEDIATA","ARS",100),("ACCIONES","A-24HS","ARS",200)],True),
    ([("ACCIONES","A-24HS","ARS",100)],True),
    ([("ACCIONES","A-24HS","ARS",100),("ACCIONES","A-48HS","ARS",100)],False),
    ([("ACCIONES","INMEDIATA","USD_MEP",100)],False),
    ([("ACCIONES","INMEDIATA","ARS",100),("CEDEARS","INMEDIATA","ARS",100)],False),
    ([("ACCIONES","INMEDIATA","ARS",100),("BONOS","INMEDIATA","ARS",200)],True),
])
def test_option_underlying_index_keeps_settlement_preference_and_ambiguity(tmp_path,underlyings,selected):
    from datetime import timedelta
    catalog=[instrument("GGAL",family,settlement=settlement,currency=currency)
             for family,settlement,currency,_ in underlyings]
    option=instrument("NON_PARSEABLE", "OPCIONES",settlement="INMEDIATA")
    catalog.append(option)
    path=fixture_db(tmp_path,catalog)
    for record,(_,_,_,price) in zip(catalog,underlyings):
        snapshot(path,record,price=price,bid=price-.01,ask=price+.01)
    snapshot(path,option,price=2,bid=2,ask=2.01,bid_size=5,ask_size=5)
    source=source_row(option,underlying=clocked("GGAL"),strike=clocked(100),
        option_right=clocked("CALL"),expires_at=clocked((AT+timedelta(days=10)).isoformat()),
        strike_unit=clocked("PER_SHARE"))
    report=families.family_reports(path,as_of=AT,catalog=catalog,sources={"family_evidence":[source]})
    universe=report["option_observation_universe"]
    assert bool(universe["selected"]) is selected
    if not selected:
        assert universe["excluded"][0]["reason_codes"]==["SOURCE_UNAVAILABLE"]


def test_option_lookup_does_not_rescan_catalogue_for_each_series(tmp_path,monkeypatch):
    from collections import defaultdict
    observed=[]
    class Counted(defaultdict):
        def __init__(self,*args,**kwargs):
            super().__init__(*args,**kwargs)
            self.full_scans=0
            observed.append(self)
        def __iter__(self):
            self.full_scans+=1
            return super().__iter__()
    catalog=[instrument("S"+str(n),"ACCIONES") for n in range(250)]
    catalog += [instrument("O"+str(n),"OPCIONES") for n in range(250)]
    path=fixture_db(tmp_path,catalog)
    monkeypatch.setattr(families,"defaultdict",Counted)
    report=families.family_reports(path,as_of=AT,catalog=catalog)
    assert len(report["instruments"])==len(catalog)
    complete=[value for value in observed if len(value)>=len(catalog)]
    assert complete and max(value.full_scans for value in complete)<=2
