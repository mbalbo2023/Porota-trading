# CHECKPOINT — WS-INSTRUMENTATION-10

- base: integration candidate after PR #391/#392.
- scope: Zero-Known-Error surface metrics only.
- no deploy.
- RCA: SCRIPT/STYLE false-positive already fixed; table-row and literal-PENDING regexes still carried legacy over-escaping and could undercount.
- fix: semantic HTML row/PENDING counts after stripping SCRIPT/STYLE.
- guard: full dashboard performance test file must pass.
- PAPER/SHADOW ONLY; PPI Watch untouched.
