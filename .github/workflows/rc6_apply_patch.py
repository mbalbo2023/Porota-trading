from pathlib import Path


def replace(path, old, new, expected=1):
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    count = text.count(old)
    if count == expected:
        p.write_text(text.replace(old, new), encoding="utf-8")
        return
    if count == 0:
        print(f"{path}: exact legacy form absent; leave current test unchanged")
        return
    raise SystemExit(f"{path}: ambiguous legacy form count={count}: {old[:100]!r}")


# Dashboard canonical wording.
replace("tests/test_dashboard_paper_v1634.py", "Operaciones cerradas recientes", "Operaciones cerradas hoy")
replace("tests/test_dashboard_session.py", "3. Decisiones — por qué aceptó o rechazó", "3. Decisiones en vivo — BUY / HOLD / abstenciones")
replace("tests/test_dashboard_session.py", "2. Operaciones cerradas recientes", "2. Operaciones cerradas hoy")

# Ledger-isolation fixture: keep DailyRisk configured but intentionally non-binding here,
# and keep existing fabricated positions marked at the same timestamp.
p = "tests/test_open_entry_terms_v17.py"
replace(p,
'''    b=PaperBroker(PaperStore(str(tmp_path/'open.db')),initial_cash='10000',daily_loss_pct=None)
    for symbol in ('ALUA','GGAL'):
        assert b._open(quote(symbol=symbol,at=AT,ask_size='100'),D('.8'),{})[0]
''',
'''    b=PaperBroker(PaperStore(str(tmp_path/'open.db')),initial_cash='10000',daily_loss_pct='100')
    for symbol in ('ALUA','GGAL'):
        q=quote(symbol=symbol,at=AT,ask_size='100')
        assert b._open(q,D('.8'),{})[0]
        b.store.add_quote(q)
''')

p = "tests/test_partial_allocation_integrity_v17.py"
replace(p,
"        b, p, q, sell = partial_spot('INMEDIATA', currency)\n",
"        b, p, q, sell = partial_spot('INMEDIATA', currency, daily_loss_pct='100')\n")
replace(p,
'''    b, bad, _, sell = sold()
    # Este caso fabrica una segunda posición para luego corromper sólo la primera.
    # La admisión/riesgo concurrente se prueba en otras suites; aquí se aísla el
    # comportamiento del ledger y del supervisor ante una posición dañada.
    b.daily_risk = None
    good_q = quote(symbol='ALUA', minute=2, ask_size='100')
''',
'''    b, bad, base_q, sell = sold()
    # Este caso fabrica una segunda posición para luego corromper sólo la primera.
    # La admisión/riesgo concurrente se prueba en otras suites; aquí se aísla el
    # comportamiento del ledger y del supervisor ante una posición dañada.
    b.store.add_quote(base_q)
    good_q = quote(symbol='ALUA', minute=2, ask_size='100')
''')
# Current RC6 fixture: the mark used for the remaining INMEDIATA position must
# preserve the complete economic identity. A default A-24HS quote is correctly
# rejected by DailyRisk as STALE_MARKS and is not a valid way to isolate ledger corruption.
replace(p,
"    b.store.add_quote(quote(symbol=bad['symbol'], minute=2, ask_size='100'))\n",
"    b.store.add_quote(replace(quote(symbol=bad['symbol'], minute=2, ask_size='100'),\n        settlement=bad['settlement'], currency=bad['currency'], market=bad['market']))\n")

p = "tests/test_production_paper_v1634.py"
replace(p,
"from bt_caucion_paper import CaucionOffer, modeled_sale_settlement, pending_proceeds\n",
"from bt_caucion_paper import CaucionOffer, modeled_sale_settlement, pending_proceeds\nfrom cf_sale_settlement import modeled_sale_settlement_date\n")
replace(p,
"    assert restarted._cash(as_of='2026-09-01T00:00:00+00:00',currency=currency)==10000+D(final['net_pnl'])\n",
"    expected_later=10000+D(final['net_pnl']) if settlement=='INMEDIATA' else before\n    assert restarted._cash(as_of='2026-09-01T00:00:00+00:00',currency=currency)==expected_later\n")
replace(p,
'''    assert rows[0]['available_at']!=rows[1]['available_at']
    assert b._cash(as_of=second.observed_at)==before+D(rows[0]['net_proceeds'])
    assert pending_proceeds(b.store,second.observed_at)==D(rows[1]['net_proceeds'])
    assert b._cash(as_of='2026-09-01T00:00:00+00:00')==before+sum(D(r['net_proceeds']) for r in rows)
''',
'''    assert rows[0]['available_at'] is None and rows[1]['available_at'] is None
    assert b._cash(as_of=second.observed_at)==before
    assert pending_proceeds(b.store,second.observed_at)==sum(D(r['net_proceeds']) for r in rows)
    assert b._cash(as_of='2026-09-01T00:00:00+00:00')==before
''')
replace(p,
'''def test_cotizacion_mep_del_catalogo_no_gasta_ars_ni_usd_generico(tmp_path, real_catalog):
    store = PaperStore(str(tmp_path / "paper.db"))
    raw = next(r for r in real_catalog if r["ticker"] == "AAPLD")
    metadata = catalog.normalize_record(raw, "INMEDIATA", "2026-08-27T13:45:03Z", "test")
    source_at = observer.now_iso()
    q = observer.normalize_quote("AAPLD", "CEDEARS", "INMEDIATA", {"price": 100, "date": source_at},
                                 {"bid": 99, "ask": 100, "bidsize": 10000, "asksize": 10000, "date": source_at}, metadata=metadata)
    broker = PaperBroker(store, initial_cash="1000000", initial_cash_usd="10000")
    assert q.currency == "USD_MEP" and q.contract.currency == "USD_MEP"
    assert broker._open(q, D("0.8"), {})[0] is False
    assert broker._cash(currency="USD") == 10000
    funded = PaperBroker(store, initial_cash_by_currency={"USD_MEP": "10000"})
    assert funded._open(q, D("0.8"), {})[0]
    p = store.open_positions()[0]
    assert p["currency"] == "USD_MEP"
    assert funded._cash() == 1000000
    assert funded._cash(currency="USD_MEP") < 10000
    ccl = replace(q, currency="USD_CCL", contract=replace(q.contract, currency="USD_CCL"))
    assert not funded._close(p, ccl, "TEST")
    closing_at = (datetime.fromisoformat(q.observed_at)+timedelta(minutes=1)).isoformat()
    closing = replace(q, bid=D("110"), ask=D("111"), observed_at=closing_at, book_at=closing_at)
    assert funded._close(p, closing, "TEST")
    closed = store.recent_closed()[0]
    assert funded._cash(currency="USD_MEP", as_of=closing.observed_at) == 10000 + D(closed["net_pnl"])
    assert funded._cash() == 1000000
''',
'''def test_cotizacion_mep_del_catalogo_no_gasta_ars_ni_usd_generico(tmp_path, real_catalog):
    store = PaperStore(str(tmp_path / "paper.db"))
    raw = next(r for r in real_catalog if r["ticker"] == "AAPLD")
    metadata = catalog.normalize_record(raw, "INMEDIATA", "2026-08-27T13:45:03Z", "test")
    source_at = observer.now_iso()
    q = observer.normalize_quote("AAPLD", "CEDEARS", "INMEDIATA", {"price": 100, "date": source_at},
                                 {"bid": 99, "ask": 100, "bidsize": 10000, "asksize": 10000, "date": source_at}, metadata=metadata)
    broker = PaperBroker(store, initial_cash="1000000", initial_cash_usd="10000")
    assert q.currency == "USD_MEP" and q.contract.currency == "USD_MEP"
    assert broker._open(q, D("0.8"), {})[0] is False
    assert broker._cash(currency="USD") == 10000
    # El capital por moneda es identidad persistente: un caso financiado usa
    # una cuenta PAPER separada, no reinterpreta la misma DB durante el día.
    funded_store = PaperStore(str(tmp_path / "funded-mep.db"))
    funded = PaperBroker(funded_store, initial_cash_by_currency={"USD_MEP": "10000"}, daily_loss_pct="100")
    assert funded._open(q, D("0.8"), {})[0]
    p = funded_store.open_positions()[0]
    assert p["currency"] == "USD_MEP"
    assert funded._cash() == 1000000
    assert funded._cash(currency="USD_MEP") < 10000
    ccl = replace(q, currency="USD_CCL", contract=replace(q.contract, currency="USD_CCL"))
    assert not funded._close(p, ccl, "TEST")
    closing_at = (datetime.fromisoformat(q.observed_at)+timedelta(minutes=1)).isoformat()
    closing = replace(q, bid=D("110"), ask=D("111"), observed_at=closing_at, book_at=closing_at)
    assert funded._close(p, closing, "TEST")
    closed = funded_store.recent_closed()[0]
    assert funded._cash(currency="USD_MEP", as_of=closing.observed_at) == 10000 + D(closed["net_pnl"])
    assert funded._cash() == 1000000
''')
replace(p,
"    broker=PaperBroker(PaperStore(str(tmp_path/'no-risk.db')))\n",
"    broker=PaperBroker(PaperStore(str(tmp_path/'no-risk.db')),daily_loss_pct=None)\n")
replace(p,
'    assert broker._cash(as_of="2026-09-01T00:00:00-03:00") == 10000 + D(broker.store.recent_closed()[0]["net_pnl"])\n',
'    assert broker._cash(as_of="2026-09-01T00:00:00-03:00") == before\n')
replace(p,
'''    # Viernes 6/11 no liquida: jueves T+1 pasa al lunes 9.
    assert modeled_sale_settlement("A-24HS", "2026-11-05T11:00:00-03:00").startswith("2026-11-09")
    assert modeled_sale_settlement("A-24HS", "2026-12-30T11:00:00-03:00") is None
    assert modeled_sale_settlement("PLAZO-DESCONOCIDO", "2026-08-28T11:00:00-03:00") is None
''',
'''    # La fecha hábil esperada es sólo diagnóstica; sin confirmación del broker
    # T+1 nunca inventa hora de acreditación ni libera caja.
    assert modeled_sale_settlement_date("A-24HS", "2026-11-05T11:00:00-03:00") == "2026-11-09"
    assert modeled_sale_settlement("A-24HS", "2026-11-05T11:00:00-03:00") is None
    assert modeled_sale_settlement_date("A-24HS", "2026-12-30T11:00:00-03:00") is None
    assert modeled_sale_settlement("PLAZO-DESCONOCIDO", "2026-08-28T11:00:00-03:00") is None
''')
replace(p,
'''    broker = PaperBroker(PaperStore(path), initial_cash="10000", risk_pct="1",
                         max_position_pct="1", max_total_exposure_pct="1", slippage_bps="0")
''',
'''    broker = PaperBroker(PaperStore(path), initial_cash="10000", risk_pct="1", daily_loss_pct="100",
                         max_position_pct="1", max_total_exposure_pct="1", slippage_bps="0")
''')
replace(p,
'''    broker = PaperBroker(store, initial_cash="1000", risk_pct="1",
                         max_position_pct="1", max_total_exposure_pct="1",
                         slippage_bps="0")
''',
'''    broker = PaperBroker(store, initial_cash="1000", risk_pct="1", daily_loss_pct="100",
                         max_position_pct="1", max_total_exposure_pct="1",
                         slippage_bps="0")
''')
replace(p,
'''    broker = PaperBroker(store, max_positions=5)
    for i in range(4):
        assert broker._open(quote(symbol=f"ABIERTA{i}"), D("0.8"), {})[0]
''',
'''    broker = PaperBroker(store, max_positions=5, daily_loss_pct="100")
    for i in range(4):
        q=quote(symbol=f"ABIERTA{i}")
        assert broker._open(q, D("0.8"), {})[0]
        store.add_quote(q)
''')
replace(p,
'''    preopen = datetime(2026, 8, 26, 10, 50, tzinfo=observer.TZ)
    opened = datetime(2026, 8, 26, 11, 5, tzinfo=observer.TZ)
''',
'''    preopen = datetime(2026, 8, 26, 10, 20, tzinfo=observer.TZ)
    opened = datetime(2026, 8, 26, 10, 35, tzinfo=observer.TZ)
''')

p = "tests/test_single_close_ledger_v17.py"
replace(p,
'''    assert b._cash(LATER,currency)==10000+D(p['net_pnl'])
    assert b._cash('2026-08-28T10:59:00-03:00',currency)==10000
''',
'''    later_expected=10000+D(p['net_pnl']) if settlement=='INMEDIATA' else before
    assert b._cash(LATER,currency)==later_expected
    assert b._cash('2026-08-28T10:59:00-03:00',currency)==10000
''')
replace(p,
'''    assert b._cash(LATER,currency)==10000+D(p['net_pnl'])


def test_reinicio_no_crea_recibo_a_partir_de_cierre_roto(closed):
''',
'''    assert b._cash(LATER,currency)==later_expected


def test_reinicio_no_crea_recibo_a_partir_de_cierre_roto(closed):
''')

print("RC6_TEST_ALIGNMENT_PATCH=OK")
