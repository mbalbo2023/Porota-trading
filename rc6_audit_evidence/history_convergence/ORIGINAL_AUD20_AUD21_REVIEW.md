# Original AUD-468-20 and AUD-468-21 evidence review

The literal 55-row convergence registry governs these two findings. This review
adds evidence links and an independent, offline Data912 replay. It adds no
original requirement or R scenario and does not change the current native-test
totals or the historical 390-node receipt union.

AUD-468-20 says that Data912 does not validate uniformly. Its closure requires
NaN/inf/future/high<low/partial-OHLC validation, precise reasons and 429/timeout
retry without false completeness. The original supplied evidence ZIP already
contains a specific prior failure: `H_DATA912_VALIDATION` completed on both the
product and c27 candidate and accepted a future date, NaN close and inconsistent
OHLC. This receipt had not been linked in the owned history matrix or remediation
register. It is not a retrospective reconstruction of an unavailable driver.

The unchanged original `d912_bad` function is independently replayed against the
complete c27 source archive. The additional sink/provider invocations use that
archive's actual adapter, sink, `HistoricalStore`, and native schema initializer.
Only the provider seam is synthetic. There is no probe DDL, source overlay,
runtime database, provider network call or financial route. Repository imports,
complete archive member hashes, and extracted source inventories are bound in
the replay receipt. Extended original NaN/Infinity JSON bytes are retained
literally as `.json.source`; interpreted observations use explicit nonfinite
tags in the strict JSON binder and new replay.

The replay distinguishes failures from successful controls. The normalizer
accepts the invalid inputs. The real sink accepts a future date, infinite
close-only value, partial OHLC and a valid-plus-future batch reported as
successful (`ok=true`, `HISTORICAL_V2_INGESTED`).
The valid control succeeds and empty control is incomplete. The sink rejects
NaN and high<low; those rejections are not product RED. Both 429 and a real
`requests.Timeout` produce five attempted calls and eventual failure without
false success. Those retry controls are not relabeled failures. The original
`S_PROVIDER_FAULTS` driver used a fake requests dependency whose
`RequestException` was `TimeoutError`; the new provider reconstruction records
its different, actual installed dependency explicitly.

AUD-468-21 is a dated coverage/observability gap, explicitly **not a READY bug**.
Its integral original `history_audit.json` receipt records 7,728 READY identities,
1,989 four-field matches and 5,739 without such a match at the historical 1/10
cut. The family sums agree. The 439,833 canonical rows and latest bar date of
28/9 are separate facts. The original audit labels the c27/current full-key
coverage unverified. No complete dataset accompanies this receipt, so this
review does not claim a new native reproduction of the original join.

The dated receipt is available and should be linked as dated observability
evidence. A missing modern coverage API on an old archive is not a native RED;
a synthetic identity collision does not reproduce the dated master cohort.
Current `coverage_inventory` guards are separate synthetic software evidence
for currency/cohort/session/consumer/as-of behavior and readiness implication
NONE. Current coverage, actual 20 trading sessions, consumer cadence, provider
availability and host deployment remain NO_VERIFICADO.

The machine-readable binding is `original_prior_evidence_binding.json`. Literal
original members and their hashes are in `original_issue468/member_inventory.json`.
The native replay is `original_data912_c27dfd9_replay.json`. Reproduce with the
provided full source archive/index or an independently verified c27 archive;
the runner rejects mismatched archive members and imported repository overlays:

```bash
PYTHONDONTWRITEBYTECODE=1 /workspace/venv_rc6/bin/python \
  rc6_audit_evidence/history_convergence/probes/original_data912_archive_probe.py \
  --source-index /tmp/rc6_finance_core_original_c27dfd9.index.json \
  --source-archive /tmp/rc6_finance_core_original_c27dfd9.tar \
  --original-driver-file rc6_audit_evidence/history_convergence/original_issue468/adversarial_harness.py.source \
  --output /tmp/rc6_original_history_data912_replay.json
```

The original received ZIP hashes and local execution integrity do not supply
independent source authentication or final-release authority. Final frozen
SHA/tree, governed JUnit/FIP, artifact and external provenance anchors remain
separate requirements.
