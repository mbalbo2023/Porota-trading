# #733 — host read-only snapshot, 25 Oct 2026

Source: [GitHub Actions run 40146181187](https://github.com/mbalbo2023/Porota-trading/actions/runs/40146181187), job 121027897382. The JSON receipt is transcribed from the sole `RC6_733_CURRENT_HOST=` line in the completed job log; candidate and tree were checked before storing it. The workflow at the diagnostic branch tip was removed after the sample. Its original commit and run remain in Git history.

Observed at `2026-10-25T00:18:54.994404+00:00`: `PASS_SNAPSHOT`, `PRODUCTION_PAPER`, `real_orders_sent=0`, market `MARKET_CLOSED`, flat spot/futures/caucion, two running owned containers on the previous image `sha256:4c91f79ce95d4219c05e09051fdb99b7304da6466742cb922f725d97d59daaa1`. Root available `7950344192` B, required pretransfer `4041528229` B; backup allowlist 0 files/0 B, general timer masked/inactive, no lock/barrier/writers. No host mutation was requested by the diagnostic action.

A point-in-time sample is not runtime validation of candidate `26a95f734cb9eece72e89a1d084c7006a1e05fa2`. Promotion must repeat admission and check PPI Watch and host ownership. See [#732 A60](https://github.com/mbalbo2023/Porota-trading/issues/732#issuecomment-6391337842).
