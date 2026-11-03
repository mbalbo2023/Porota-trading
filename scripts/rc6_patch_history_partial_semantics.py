from pathlib import Path

p = Path('bf_production_paper_observer.py')
s = p.read_text(encoding='utf-8')

marker = '''def _download_histories(reader, store):\n'''
helper = '''def _history_batch_semantics(statuses):\n    \"\"\"Resume calidad de lote sin llamar fallo a PARTIAL con evidencia válida.\n\n    Esta función sólo corrige observabilidad/source_sync. No modifica aceptación,\n    canonicalización, provenance, retries ni presión sobre PPI.\n    \"\"\"\n    statuses = tuple(str(value or \"\").upper() for value in statuses)\n    full_valid = sum(value == \"VALID_PAYLOAD\" for value in statuses)\n    partial_with_valid_evidence = sum(value == \"PARTIAL\" for value in statuses)\n    empty_invalid = sum(value == \"EMPTY_OR_INVALID\" for value in statuses)\n    errors = sum(value == \"ERROR\" for value in statuses)\n    known = full_valid + partial_with_valid_evidence + empty_invalid + errors\n    if known != len(statuses):\n        raise ValueError(\"PPI_HISTORY_BATCH_UNKNOWN_STATUS\")\n    usable = full_valid + partial_with_valid_evidence\n    hard_failures = empty_invalid + errors\n    if full_valid and not partial_with_valid_evidence and not hard_failures:\n        state = \"VERDE\"\n    elif usable:\n        state = \"AMARILLO\"\n    else:\n        state = \"ROJO\"\n    return {\n        \"full_valid\": full_valid,\n        \"partial_with_valid_evidence\": partial_with_valid_evidence,\n        \"empty_invalid\": empty_invalid,\n        \"errors\": errors,\n        \"usable\": usable,\n        \"hard_failures\": hard_failures,\n        \"state\": state,\n    }\n\n\n'''
if '_history_batch_semantics' not in s:
    if marker not in s:
        raise SystemExit('MARKER_DOWNLOAD_HISTORIES_NOT_FOUND')
    s = s.replace(marker, helper + marker, 1)

old = '''    total = successes = failures = 0\n    all_symbols = _historical_targets(store)\n'''
new = '''    total = 0\n    batch_statuses = []\n    all_symbols = _historical_targets(store)\n'''
if old not in s:
    raise SystemExit('COUNTERS_MARKER_NOT_FOUND')
s = s.replace(old, new, 1)

old = '''            total += count\n            if status=='VALID_PAYLOAD':\n                successes += 1\n            else:\n                failures += 1\n        except Exception as exc:\n            failures += 1\n'''
new = '''            total += count\n            batch_statuses.append(status)\n        except Exception as exc:\n            batch_statuses.append(\"ERROR\")\n'''
if old not in s:
    raise SystemExit('LOOP_STATUS_MARKER_NOT_FOUND')
s = s.replace(old, new, 1)

old = '''    state = \"VERDE\" if successes and not failures else \"AMARILLO\" if successes else \"ROJO\"\n    with store.connect() as c:\n        covered = c.execute(\"SELECT COUNT(*) FROM production_history WHERE row_count>0\").fetchone()[0]\n    detail = (f\"Lote histórico {successes}/{len(symbols)}; cobertura acumulada \"\n              f\"{covered}/{len(all_symbols)} instrumentos; {total} filas en este lote; \"\n              f\"{failures} fallidos. La descarga completa es incremental para no saturar PPI.\")\n    _sync_state(store, \"PPI_PRODUCTION_HISTORY\", state, total, detail,\n                success=bool(successes))\n    _health(store, \"PPI_PRODUCTION_HISTORY\", state, detail, \"PPI Producción\",\n            success=bool(successes))\n'''
new = '''    semantics = _history_batch_semantics(batch_statuses)\n    state = semantics[\"state\"]\n    with store.connect() as c:\n        covered = c.execute(\"SELECT COUNT(*) FROM production_history WHERE row_count>0\").fetchone()[0]\n    detail = (\n        f\"Lote histórico: completos={semantics['full_valid']}; \"\n        f\"parciales_con_evidencia_valida={semantics['partial_with_valid_evidence']}; \"\n        f\"vacíos_o_inválidos={semantics['empty_invalid']}; errores={semantics['errors']}; \"\n        f\"cobertura acumulada {covered}/{len(all_symbols)} instrumentos; \"\n        f\"{total} filas válidas en este lote. PARTIAL conserva evidencia válida pero \"\n        f\"permanece AMARILLO; sólo vacío/inválido y ERROR son fallos duros. \"\n        f\"La descarga completa es incremental para no saturar PPI.\"\n    )\n    usable = bool(semantics[\"usable\"])\n    _sync_state(store, \"PPI_PRODUCTION_HISTORY\", state, total, detail, success=usable)\n    _health(store, \"PPI_PRODUCTION_HISTORY\", state, detail, \"PPI Producción\",\n            success=usable)\n'''
if old not in s:
    raise SystemExit('FINAL_SEMANTICS_MARKER_NOT_FOUND')
s = s.replace(old, new, 1)

p.write_text(s, encoding='utf-8')
print('HISTORY_PARTIAL_SEMANTICS_PATCH=APPLIED')
