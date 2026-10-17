"""Numeric-only causal control of bootstrap reader versus owner commit."""
import json,multiprocessing,pathlib,sqlite3,tempfile,time
from contextlib import closing
from tests.test_issue465_budget_adversarial import Clock,exit_floor,book_payload
from rc6_ppi_global_budget import GlobalPPIBudget,BudgetBackpressure

def owner(path,config,at,ready,release,output,calls):
    clock=Clock(at);budget=GlobalPPIBudget(path,config,clock=clock.now)
    def fetch():
        with calls.get_lock():calls.value+=1
        ready.set();assert release.wait(5)
        return book_payload(clock)
    try:
        value=budget.coalesced_book(("GGAL","ACCIONES","BYMA","ARS","A-24HS"),fetch,
             consumer="SCANNER",priority="OPENED_CRITICAL")
        output.put({"role":"owner","result":"SERVED","date":value["date"]})
    except BudgetBackpressure as error:
        cause=error.__cause__
        output.put({"role":"owner","result":str(error),
                    "cause_class":type(cause).__name__ if cause else None,
                    "sqlite_errorcode":getattr(cause,"sqlite_errorcode",None)})

def follower(path,config,at,reader_started,reader_release,output):
    clock=Clock(at);original=GlobalPPIBudget._get;held=False
    def held_get(connection,key,default=None):
        nonlocal held
        value=original(connection,key,default)
        if key=="schema" and not held:
            held=True;reader_started.set();assert reader_release.wait(5)
        return value
    GlobalPPIBudget._get=staticmethod(held_get)
    budget=GlobalPPIBudget(path,config,clock=clock.now)
    GlobalPPIBudget._get=staticmethod(original)
    def forbidden():raise AssertionError("DUPLICATE_FETCH")
    try:
        value=budget.coalesced_book(("GGAL","ACCIONES","BYMA","ARS","A-24HS"),forbidden,
             consumer="EXIT_READER",priority="EXIT_CRITICAL")
        output.put({"role":"follower","result":"SERVED"})
    except BudgetBackpressure as error:
        output.put({"role":"follower","result":str(error)})

with tempfile.TemporaryDirectory(prefix="single-flight-causal-",dir="/work") as temporary:
    clock=Clock();config=exit_floor(clock,common=1);path=str(pathlib.Path(temporary)/"budget.sqlite")
    GlobalPPIBudget(path,config,clock=clock.now)
    context=multiprocessing.get_context("fork")
    ready,release,reader_started,reader_release=[context.Event() for _ in range(4)]
    output=context.Queue();calls=context.Value("i",0)
    first=context.Process(target=owner,args=(path,config,clock.now().isoformat(),ready,release,output,calls))
    second=context.Process(target=follower,args=(path,config,clock.now().isoformat(),reader_started,reader_release,output))
    try:
        first.start();assert ready.wait(5)
        second.start();assert reader_started.wait(5)
        release.set()
        failed=output.get(timeout=3)
        assert failed["role"]=="owner" and failed["result"]=="PPI_BUDGET_STATE_UNAVAILABLE",failed
        assert failed["cause_class"]=="OperationalError" and failed["sqlite_errorcode"] in (5,6),failed
        reader_release.set()
        followed=output.get(timeout=8)
        assert followed=={"role":"follower","result":"PPI_BOOK_EXIT_DEADLINE_EXCEEDED"},followed
        for process in (first,second):
            process.join(timeout=5);assert process.exitcode==0
        assert calls.value==1
        receipt={"schema":"rc6.single-flight-bootstrap-control.v1","injected_bootstrap_read_snapshot":True,
                 "owner":failed,"follower":followed,"fetch_count":calls.value,
                 "production_budget_code_changed":False,"real_orders_sent":0,
                 "production_data_mounted":False,"droplet_accessed":False,"status":"EXPECTED_FAIL_CLOSED"}
        pathlib.Path("/work/single-flight-causal.json").write_text(json.dumps(receipt,indent=2,sort_keys=True)+"\n")
        print("RC6_SINGLE_FLIGHT_CAUSAL="+json.dumps(receipt,sort_keys=True),flush=True)
    finally:
        release.set();reader_release.set()
        for process in (first,second):
            if process.pid is not None:
                process.join(timeout=2)
                if process.is_alive():process.terminate();process.join(timeout=2)
