# RC4 WIP — `/vivo` route/UI reconciliation

## Root cause verified from candidate1 operative source

Candidate1 currently contains three related views in `bg_paper_dashboard.py`:

- `paper_page(compact=True)`: state/cards + recent decisions; **no open-position list**.
- `live_page()`: open positions + PnL + supervision + book timestamp; **no per-trade drill-down and no PnL semantic color class**.
- `motor_page()`: per-trade `<details class='paper-trade'>` drill-down + positive/negative PnL classes + gates/features/forensics.

The route installer maps:

`/vivo -> lambda: paper_page(True)`

while `/en-vivo -> live_page`.

Therefore the historic `/vivo` endpoint no longer exposes the operational position experience the user expects even though much of the required code still exists elsewhere.

## RC4 required behavior

Create one canonical live-operation view and avoid parallel semantics.

Preferred contract:

- `/vivo` = canonical operational live page.
- `/en-vivo` may redirect/alias to `/vivo` for backward compatibility.
- summary row per open trade:
  - expand/collapse control;
  - symbol;
  - family/currency/settlement;
  - quantity;
  - entry;
  - current mark;
  - current PnL;
  - semantic state `GANANCIA` / `PERDIDA` / `NEUTRO` / `STALE`;
  - last mark timestamp;
  - mark age/freshness;
  - exit-supervision state.
- PnL positive: green + text; negative: red + text. Never communicate meaning only with color.
- stale mark: yellow/STALE and do not present value as current without warning.
- drill-down reuses existing motor-page evidence:
  - opened_at;
  - entry/mark;
  - gross/net PnL;
  - costs/slippage;
  - stop/target;
  - technical/economic/patrimonial gates;
  - gate explanation;
  - dynamic concurrent-risk snapshot from `features_json` when present;
  - exit intent/supervision;
  - forensic stop/exit detail;
  - mark/book timestamp.

## Safety

Dashboard remains read-only. No network order path, no mutation, no PPI order action, no DB write.

## Tests required

1. `/vivo` contains every open PAPER position.
2. Each open position has an expandable detail element.
3. Positive PnL renders positive class + `GANANCIA` text.
4. Negative PnL renders negative class + `PERDIDA` text.
5. Stale/no mark never renders as fresh/current.
6. Last update is the persisted mark/book timestamp, not page-render time.
7. PnL currencies are never aggregated across ARS/USD variants.
8. `real_orders_sent=0` invariant remains visible.
9. `/en-vivo` compatibility does not create divergent logic.
10. Tablet/mobile markup preserves expand/collapse usability.

**WIP only — no runtime deploy during market session.**
