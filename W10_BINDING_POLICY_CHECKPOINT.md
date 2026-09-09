# W10 RC6 — Sector Concentration BINDING Gate

Canonical requirement: sector concentration is **BINDING** for new PAPER entries.

The current engine wiring already passes sector context into the policy gate. W10 is not deploy-ready until a semantic test proves:
- a candidate that breaches sector concentration is rejected for NEW entry;
- valid candidates remain evaluable;
- exits/supervision of existing positions are not blocked by the entry concentration gate;
- no real order route is exercised.
