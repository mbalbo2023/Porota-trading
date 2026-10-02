from pathlib import Path


def replace(path, old, new):
    p = Path(path)
    text = p.read_text()
    assert text.count(old) == 1, (path, old[:80], text.count(old))
    p.write_text(text.replace(old, new))


def block(path, start, end, replacement):
    p = Path(path)
    text = p.read_text()
    assert text.count(start) == 1, (path, start)
    i = text.index(start)
    j = text.index(end, i)
    p.write_text(text[:i] + replacement + text[j:])


block('bg_paper_dashboard.py', '        gdelt_risk=features.get(',
      '        exit_policy=features.get(',
      '        # Retired provider: historical features remain in the forensic variables.\n        gdelt_risk_html=""\n')
block('bg_paper_dashboard.py', '    try:\n        import rc6_gdelt_shadow',
      '    macro_html = ""',
      '    # GDELT is retired; do not query it or render an active provider card.\n    gdelt_html = ""\n')
block('be_paper_engine.py', '        # Noticias GDELT:',
      '        return "BUY", score, "Momentum positivo y friccion admisible", features',
      '''        # Noticias GDELT: retired; no collector, cache read or decision effect.
        features["gdelt_risk_shadow"] = {
            "mode": "RETIRED", "state": "DEPRECATED_EXCLUDED",
            "reason": "RETIRED_OPERATOR_DECISION_2026-10-02",
            "decision_effect": "EXCLUDED", "source": "NONE",
        }
''')
replace('di_caucion_cash_sweep_runtime_hf6.py',
        'and offer.paper_fill_policy == "CONSERVATIVE_NOTIONAL_CAP"',
        'and offer.paper_fill_policy in {"CONSERVATIVE_NOTIONAL_CAP", "LIVE_PPI_BID_PARTICIPATION_CAP"}')
replace('di_caucion_cash_sweep_runtime_hf6.py',
        '    from rc6_cauciones_contract import parse_ticker, theoretical_liquidity_date\n',
        '    from rc6_cauciones_contract import parse_ticker, theoretical_liquidity_date\n    from cp_contract_evidence_v2_hf6 import pending_material_changes\n    from dj_caucion_live_ppi_rc6 import SOURCE as PPI_BOOK_SOURCE\n')
replace('di_caucion_cash_sweep_runtime_hf6.py',
        '        rows=[dict(r) for r in connection.execute("""SELECT\n          f.ticker',
        '        # The catalog can lag evidence ingestion. Recheck material changes\n        # from the append-only evidence log instead of trusting stale READY.\n        pending = pending_material_changes(connection)\n        rows=[dict(r) for r in connection.execute("""SELECT\n          f.ticker')
replace('di_caucion_cash_sweep_runtime_hf6.py',
        '    for row in rows:\n        if row["state"]!="BOOK_READY":',
        '''    for row in rows:
        identity_key = ("CAUCIONES", row["ticker"], row["market"],
                        row["currency"], row["settlement"])
        if identity_key in pending:
            errors.append(f"{row['ticker']}:CHANGED_REVIEW_REQUIRED")
            continue
        if row["source"] != PPI_BOOK_SOURCE:
            errors.append(f"{row['ticker']}:CAUCION_LIVE_SOURCE_UNVERIFIED")
            continue
        if row["state"]!="BOOK_READY":''')
replace('zz_wave8_dashboard_live_rc6.py',
        r'\s*MAE\s*=\s*0(?:[\.,]0{1,8})?',
        r'\s*MAE\s*=\s*0(?:[\.,]0{1,8})?(?![\d.,])')
replace('zz_wave8_dashboard_live_rc6.py',
        r'r"causa\s+STOP_PAPER",',
        r'r"causa\s+STOP_PAPER(?!\s*\(el bid fresco)",')
