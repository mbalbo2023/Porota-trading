# POROTA 10:16 Preopen verification
UTC=2026-09-14T13:16:05+00:00
MUTATIONS=READ_ONLY_CHECKS_ONLY

## Legacy historical store
mode=444 size=582156288 mtime=2026-09-14 11:07:33.933805832 +0000 file=/opt/porota-trading/data/market_history.db

## Possible writers

## Observer PAPER state
["PRODUCTION_PAPER", "READY_PREOPEN", "PREOPEN", "2026-09-14T13:16:01.915243+00:00", 0]

## Worker processes
2059686 2059662       36:09 Ss   python bv_paper_runtime.py
2059713 2059686       36:08 S    /usr/local/bin/python /app/bv_paper_runtime.py --exit-reader
2059714 2059686       36:08 S    /usr/local/bin/python /app/bv_paper_runtime.py --notification-worker
2059715 2059686       36:08 S    /usr/local/bin/python /app/bv_paper_runtime.py --candle-worker
2059716 2059686       36:08 S    /usr/local/bin/python /app/bv_paper_runtime.py --intraday-scalping-worker
