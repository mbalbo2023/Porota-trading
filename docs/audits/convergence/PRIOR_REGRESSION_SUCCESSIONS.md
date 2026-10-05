# Five typed Issue465 regression successions

State: SOURCE_ONLY_NOT_EXECUTED. This isolated workstream starts at ROOT
9d51af4585829ecfb1a734717638f806bf80e3ed. No helper tests were run before
this source-only commit because ROOT reserved the CPU window.

The original c27 Issue465 matrix names 442 distinct native test functions.
Five functions have an explicitly authorized contract replacement. The other
437 remain subject to the original gate and its original case minimums.
These relations add no original requirement, attack ID or independent scenario.

| Original function | Authorized successor function | Required parent |
| --- | --- | --- |
| test_tighter_global_cap_exposes_unserviceable_demand_without_lower_borrow | test_tighter_global_cap_rejects_unserviceable_exit_demand_before_bootstrap | U05 |
| test_approved_dynamic_reserves_real_durable_positions_without_paper_authority_change | test_approved_dynamic_rejects_insufficient_capacity_for_real_durable_positions | U05 |
| test_stale_current_returns_complete_prior_cut_without_borrowing_newer_members | test_stale_current_is_rejected_by_durable_high_water_without_borrowing_newer_members | U20 |
| test_acknowledged_unpinned_generation_rotates_but_ack_ledger_remains | test_acknowledged_unpinned_generation_rotates_and_receipt_survives_ack_compaction | U15 and U18 |
| test_database_lock_short_timeout_does_not_publish | test_dirty_source_transaction_does_not_publish | U24 |

The module exports an immutable SUCCESSORS mapping and
verify_successions(original_matrix, original_sources, current_sources,
requirements, executed). It raises SuccessionError, a ValueError subclass,
when a relation lacks its original source, authorized current function, parent
guard declaration or required case count. It performs no Git, filesystem,
network or test execution.

Each source mapping is keyed by the exact test path and contains blob,
git_mode and immutable data bytes. The helper recomputes the Git SHA1 blob
header hash and file SHA256, rejects unsupported modes and syntax, and requires
unique top-level native function definitions. ROOT must supply these records
from c27 and the actual candidate Git tree; the helper does not authenticate
the caller's Git tree or original matrix independently.

The selected function is the original node when it remains uniquely defined.
Otherwise only its fixed successor is allowed. Every linked parent row must
include that selected node in test_nodes. There is no fuzzy, parameter-name or
unrelated-function alias. Duplicate original coverage retains its greatest
minimum_cases; each minimum must be an integer from 1 through 100.

Provided execution counts must be integers at least equal to the original
minimum. An executed=None inventory returns NOT_EXECUTED, never an executed
GREEN claim. The helper verifies the five relations only; ROOT still validates
the immutable JUnit, all other old coverage and normal FIP path evolution.

EXACT_AST and UPDATED_AST distinguish structural hashes when the old node is
retained. Neither state proves semantic equivalence, financial behavior or
literal file preservation. UPDATED_AST requires ROOT's normal path-evolution
explanation and guards. TYPED_SUCCESSOR identifies one of the five declared
contract changes and reports both original and current anchors and AST hashes.

The JSON records real source definitions from c27 and 3cfe112bb8061c5492d078e15a7ab41f698e2b0f,
plus the five selected preexisting PASS case families from the supplied prior
JUnit. That JUnit's SHA256 is f61b24e4e2f5637d9721c6e778d2b71869e1c0ad9cbc1d2d9d4b4984ca5d820b.
Its recorded suite has 4,802 cases; this document neither reruns it nor applies
its verdict to the new helper or final candidate. The native metadata test uses
controlled parent declarations and explicitly verifies that boundary. Full
parent requirement wiring, fresh governed execution, frozen FIP/artifact and
external runtime evidence remain ROOT's responsibilities.
