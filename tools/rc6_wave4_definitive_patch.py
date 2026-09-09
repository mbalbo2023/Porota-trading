from pathlib import Path

p=Path('bf_production_paper_observer.py')
s=p.read_text()
old="""            from bl_candle_engine import archive_raw, canonical
            metadata = financial_catalog.lookup(store,symbol,instrument_type,settlement)
            expected = len(payload) if isinstance(payload,list) else 0
            status = 'VALID_PAYLOAD' if count and count==expected else 'PARTIAL' if count else 'EMPTY_OR_INVALID'
            with store.connect() as c:
                c.execute('BEGIN IMMEDIATE')
                archive_raw(c,origin='PPI_HISTORY',row_key=canonical([symbol,instrument_type,settlement,attempted]),
                    payload={'symbol':symbol,'asset_class':instrument_type,'settlement':settlement,
                        'date_from':start.isoformat(),'date_to':end.isoformat(),'metadata':metadata,
                        'valid_rows':count,'payload_json':json.dumps(payload,ensure_ascii=False,default=str)},
                    recorded_at=attempted,quality=status)
                c.execute('INSERT OR REPLACE INTO production_history_attempts VALUES(?,?,?,?,?,?,?)',
"""
new="""            from bl_candle_engine import canonical
            from fb_raw_evidence_exact_v1 import enabled as exact_evidence_enabled, archive_wrapper as archive_exact_wrapper
            metadata = financial_catalog.lookup(store,symbol,instrument_type,settlement)
            expected = len(payload) if isinstance(payload,list) else 0
            status = 'VALID_PAYLOAD' if count and count==expected else 'PARTIAL' if count else 'EMPTY_OR_INVALID'
            row_key = canonical([symbol,instrument_type,settlement,attempted])
            history_wrapper = {'symbol':symbol,'asset_class':instrument_type,'settlement':settlement,
                        'date_from':start.isoformat(),'date_to':end.isoformat(),'metadata':metadata,
                        'valid_rows':count,'payload_json':json.dumps(payload,ensure_ascii=False,default=str)}
            external_exact = exact_evidence_enabled()
            if external_exact:
                archive_exact_wrapper(row_key=row_key, wrapper=history_wrapper,
                                      recorded_at=attempted, quality=status)
            with store.connect() as c:
                c.execute('BEGIN IMMEDIATE')
                if not external_exact:
                    from bl_candle_engine import archive_raw
                    archive_raw(c,origin='PPI_HISTORY',row_key=row_key,payload=history_wrapper,
                                recorded_at=attempted,quality=status)
                c.execute('INSERT OR REPLACE INTO production_history_attempts VALUES(?,?,?,?,?,?,?)',
"""
if s.count(old) != 1:
    raise SystemExit(f'OBSERVER_PATCH_ANCHOR_COUNT={s.count(old)}')
p.write_text(s.replace(old,new,1))

p=Path('porota_mode_manager.py')
s=p.read_text()
anchor='    "PPI_BACKGROUND_INGEST_SECONDS": "7200",\n    "PAPER_NEWS_INGEST_ENABLED": "false",\n'
repl=('    "PPI_BACKGROUND_INGEST_SECONDS": "7200",\n'
      '    "PPI_HISTORY_RAW_STORAGE_MODE": "EXTERNAL_EXACT_V1",\n'
      '    "PPI_HISTORY_EVIDENCE_ROOT": "/app/data/evidence/ppi_history_exact_v1",\n'
      '    "PAPER_NEWS_INGEST_ENABLED": "false",\n')
if s.count(anchor) < 2:
    raise SystemExit(f'MODE_PATCH_ANCHOR_COUNT={s.count(anchor)}')
p.write_text(s.replace(anchor,repl))
print('PATCH_MATERIALIZED=GREEN')
