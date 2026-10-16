# Issue 600 — control plane, 2026-10-16

Base main: `4fa676ea3d6ccdf0c0e8eddc6dc7799427a16ce0`.
Product baseline verified: `fa751eb1f985b55f6aeb6729704452150c93c5e4`.
Owner: WS-RC6-POSTCLOSE-CONSOLIDATED-20261016, single integrator.

This branch is NOT a production artifact and must not trigger Deploy V2.
No merge to main during the market session. Product fixes are separate.

| Item | RCA | Fix / guard | Remaining evidence |
|---|---|---|---|
| M1 | Freshness depended only on the component's own OPEN state | Independently load deployed audited BYMA calendar and spot hours in a bounded interpreter; UNKNOWN explicitly escalates; false CLOSED cannot suppress stale inputs | Real postmerge OPEN observation |
| M2 | Genuine schedule cadence not proven; dispatch loss root cause unknown | Query genuine completed schedule events, excluding pushes/manual retries; flag silence beyond two intervals | Three actual consecutive schedules; independent watchdog ACK still NO_VERIFICADO |
| M3 | SQLITE_INTERRUPT in runtime.py:54 during OPEN; global 2s expires in INTRADAY_POINTS | Expose allowlisted SQL stage/deadline from existing durable status, without query replay or heartbeat fabrication | Runtime validation of product fix |
| D1 | HTTP200 does not establish correct statuses | Count explicit visible status badges per route, no HTML/account export; critical routes intraday and full menu postclose | Postclose 50-route sample and visual tablet QA |

## Real evidence

- Market Watch run 38819767035 attempt 6, artifact 12056579122, SHA256 `a0b8536002188cfed940dfbd7a0f6cb8f1bc69ee115b6b9a265abeb0294525e1`: 15:51:19Z / 12:51 ART, MARKET_OPEN, PAPER/0, observer heartbeat age 29s, marketdata age 41s, SHADOW SQLITE_INTERRUPT. Three containers running; observer OOM bit set, but FinishedAt 04:15 precedes StartedAt 04:58, so current OOM attribution is NO_VERIFICADO.
- Telegram real receipt: message_id 967, SENT. This does not certify future delivery or cron cadence.
- Second OPEN sample run 38819767035 attempt 7 repeats SHADOW failure, candle cursor advances 239009 to 239079.
- Existing manual diagnostic run 38817987859 rerun: artifact 12057693279, digest `2bd4861a45ac08fec84de713fe71fd2d3cfbd83b3ce4fe3a8c21b40060094908`; stage probe CONTAINER_PROBE_TIMEOUT, schedule API probe unavailable; no Docker events or nonzero current exit observed. Do not convert probe failure into system health.
- Isolated monitor run 38930121732, artifact 12058816231, digest `d8e6727e04f68b7efeda4101b856e9508ccd1d6df2ac0527a0dbcb950407c8fd`: independently OPEN at 16:12:52Z. Catalogue 14606/873.114ms, contract state 2424/289.492ms, intraday 958/398.797ms followed by deadline SQLITE_INTERRUPT. Candle cursor239239; scalping RUNNING24/0. These values establish the read-budget mechanism, not a database-lock diagnosis.

## Existing fallback, not a second scheduler

Product already contains rc6_market_watch_silence.py and governed systemd service/timer. Its installation/ACK must be measured directly. This monitor now reads the timer's active state, bounded service result/timestamps and allowlisted JSON receipt (status, ACK time, message_id). An old ACK is explicitly aged and never becomes proof of three scheduled probes. Product candidate guards queued/in-progress/retried events and holidays in that existing mechanism; no duplicate fallback or fast-health integration is shipped.

Local contracts after this extension: 37 monitor + 3 dashboard tests PASS. Exact native Actions results remain separate. Visual tablet QA and full postclose audit remain NO_VERIFICADO; an HTTP200 is not a semantic or visual approval.

## Semantics and limits

Calendar authority is scoped to BYMA PAPER spot, independently of observer_state. Unavailable/unreviewed calendar yields UNKNOWN. A source access check is not quote freshness or trading authority. A missing schedule is detectable on the next wake; this monitor alone cannot prove an independent watchdog was alive during its own absence.

No REAL paths, broker calls, runtime writes, provider activation or PPI Watch access are added. All remote probes remain bounded/read-only. Existing Telegram transition transport is unchanged. NO_GO if required market/ownership/data/gates cannot be proven.
