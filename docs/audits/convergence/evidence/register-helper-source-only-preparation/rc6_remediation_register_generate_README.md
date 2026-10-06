# External documentary register rebind

ROOT must first authorize a stable final documentary source S. Then run the stdlib-only helper with that explicit immutable source and an output outside all Git worktrees:

```sh
PYTHONDONTWRITEBYTECODE=1 python /tmp/rc6_remediation_register_generate.py \
  --repo /workspace/porota_rc6_convergence \
  --source FULL_STABLE_SOURCE_S_SHA \
  --layout derived-ref \
  --out /tmp/RC6_SOURCE_S_REGISTER.json
```

The earlier expanded diagnostic `/tmp/rc6_remediation_register_48a_diagnostic.json` is preserved without regeneration. Its source is `48a85471b1e420ee5e8e17ae77f121d13f67e5ad`, tree `5ebec14cc01e5337a94fa53147eb8362cc38195e`. It is an intermediate documentary snapshot. ROOT already advanced during preparation; this output therefore requires rebinding before any final freeze. The placeholders in the commands are intentionally not an executable final-source selection.

The CLI reads only Git regular blobs/modes through `git --no-replace-objects --no-optional-locks`. It validates raw Git blob object hashes. It does not read workingtree overlays, `/tmp` receipts, owner worktrees, product modules, provider data, SQLite databases or network sources. It executes no native guards, Gov, Docker, runtime or deployment. The earlier generator is preserved at `/tmp/rc6_remediation_register_generate.stale87.source.py`.

Schemas come from the selected source: `rc6.remediation-register.v1`, the current closure matrix, and published owner mappings. Requirements use `workstream` to select their owner mapping when the closure omits `owner_mapping`. Scenario rows may contain either `execution_status` or a disposition; omission of execution status is reported as `NOT_DECLARED_IN_CLOSURE`, never PASS. Additional finding counts and path inventory sizes are dynamic.

The previous committed register supplies immutable historical IDs, reviewed clause-specific receipt references and classifications. Each original receipt keeps its original byte hash/length, capture source, environment, scope and case counts. Current publication bindings are separate. Full JSON metadata and raw XML suite/case metadata are retained without interpreting publication at the new source as new execution. Local-only historical records remain records from the committed register; their bytes are not silently imported from the workspace. Changed mapping/definition records preserve their prior definition separately. Changed raw receipts retain their historical committed bytes when available and acquire a distinct catalog entry for the new bytes. No receipt counts are summed.

SourceSnapshot describes the non-circular order: select source S/tree T; document commit D references S; freeze C after D; actual Gov/FIP/JUnit/image/artifact attest C. Final SHA/tree, FIP, JUnit, artifact and external anchor fields remain null in this documentary output. Rebinding is required whenever ROOT advances. The current output does not include its own hash. An old register blob at S, when present in `path_evolution`, is explicitly a previous-template input rather than this future output.

Diagnostic source inventory: 55 original requirements, 80 original scenarios, 90 front variants, six restored controls, 18 additional findings, 581 path-evolution records, 715 distinct native AST declarations and 321 catalog entries. These are documentary inventory counts, not tests run. 303 catalog records have committed raw bytes; 18 historical local records are preserved without reading local files. Rejection controls checked a ROOT output path and an abbreviated source SHA. No ROOT file was written.

Six findings declare a parent owner index rather than a typed exact new-finding ID: `NEW_ARCHIVE_DIRECTORY_ATIME_MUTATION`, `NEW_CANONICAL_LIVE_FILE_QUOTA_DRIFT`, `NEW_GIT_REPLACEMENT_OBJECT_AUTHORITY`, `NEW_INSTALLED_DISTRIBUTION_METADATA_DUPLICATE`, `NEW_SIGNED_ZERO_SUBTREE_COLLAPSE`, `NEW_SOURCE_GIT_MODE_EVOLUTION`. Their ROOT clauses, source bytes and native AST guards are present; the helper exposes the narrower owner-membership limit rather than inventing membership or claiming a product failure. Budget's `retention_capacity_followup` is a verified named section; two supplemental controller guards belong to ROOT closure. Source-copy ownership uses `ROOT_INTEGRATION_SUPPLEMENTAL_SOURCE_MAPPING`, separate from History's older owned matrix.

AUD20's three shared Inf guard references retain precisely one variant and eight original execution references each. Reviewed AUD21 caller boundaries are preserved; the current canonical caller is copied from the selected closure. The U14/UX material dispositions at S remain documentary pending limits, not final release authority.

Output returns `DOCUMENTARY_REBIND_GENERATED_NOT_EXECUTED` plus explicit issues. Any error rejects the rebind; output is written atomically outside the repository. This helper does not replace the canonical FIP/Gov/artifact validation.

## Compact derived layout, prepared after the 48a diagnostic

The default is now `--layout derived-ref`, schema `rc6.remediation-register.v2`. The 48a JSON above remains the old expanded documentary diagnostic and was not regenerated after this change. `--layout expanded` explicitly selects legacy inline v1. Do not generate either against a moving final source; ROOT will provide stable source S.

V2 keeps `evidence_catalog.E001` through `E210` as the exact original objects, including all capture sources, environments, scope, counts, hashes and older case arrays. `original_catalog_preservation` binds their canonical value hash to the immutable input-register Git member. Updated current definitions and added metadata live separately in `current_evidence_derivation`; they cannot overwrite original210 fields in the compact document. Expanding the derived overlays reconstructs the prior v1 documentary values.

Only added copies of JSON/XML metadata and new file/AST inventories are references. RAW refs bind complete publication SHA/tree/blob, regular mode100644/100755, SHA256 and exact byte length. Local derived refs bind their exact JSON value hash, including signed zero and scalar types. Missing members, changed modes, unknown schemas, alias paths, altered payload hashes, cycles, changed original fields or another source snapshot reject resolution. RAW receipt objects and original catalog objects remain literal data, including any ref-shaped values they contain; those values cannot become control instructions.

Verify an external compact JSON without generating a new register:

```sh
PYTHONDONTWRITEBYTECODE=1 python /tmp/rc6_remediation_register_generate.py \
  --repo /workspace/porota_rc6_convergence \
  --source FULL_SOURCE_S_SHA \
  --verify-refs /tmp/FINAL_EXTERNAL_COMPACT_REGISTER.json
```

The standalone stdlib resolver is embedded in the same helper (`RefResolver`, `RefResolutionError`, `compact_derived`). It reads immutable Git objects, not workingtree overlays. All referenced RAW members must remain available in that source repository. A tablet/artifact distribution without Git objects needs the exact RAW member inventory plus separately verified source association before it can resolve them; the helper does not grant authority to an unsigned manifest or a missing member. The canonical source bundle already carries committed RAW files, but historical referenced versions must also be preserved when packaging them. Never discard RAW after producing a compact projection.

Generation of v2 automatically verifies all derived references and checks exact documentary expansion before atomic output. This verification is Git/JSON/XML only; it never declares current native guard/Gov execution. Final SHA/FIP/JUnit/artifact/external anchor fields remain null. Source advances still require rebind in the S→D→C order.

The six named ROOT supplements use `rc6.root-supplemental-named-finding-bindings.v1` with typed `exact_root_named_membership=True` and `exact_upstream_owner_membership=False`. The helper recognizes only explicit `ROOT_INTEGRATION_SUPPLEMENTAL_NAMED_FINDING_BINDINGS`. It verifies exact original parents, current code paths and unique native AST, actual old owner index absence, old owner path/workstream and unmodified original RAW metadata. This category is ROOT supplementary membership; it does not claim exact membership in the original owner index or new executed GREEN.

Source-only private controls are recorded in `/tmp/rc6_register_derived_refs_source_only_controls.json` and their driver in `/tmp/rc6_register_derived_refs_source_only_controls.py`. They use small explicit Git/JSON/XML fixtures; no product module, native producer, Gov, image, provider, runtime or financial execution. They are not new original audit attacks or a final-source execution receipt.

## Known compressed XML and receipt scope

Only exact `.xml` and single-member `.xml.gz` files are decoded. Gzip decoding checks the CRC, complete stream, absence of concatenated members/trailing bytes and a 32 MiB uncompressed bound. The derived encoding record retains the actual gzip mtime, compressed SHA256/bytes and uncompressed SHA256/bytes. A declared `gzip-mtime0` requires the actual mtime to be zero. Arbitrary ZIP archives are unsupported.

The original `raw_refs` metadata remains verbatim. The supplemental helper resolves only XML member paths explicitly named in that mapping and verifies any declared compressed/uncompressed hash and length. Absolute capture paths remain literal metadata; their local bytes are never read. Missing `.xml.gz` members are unresolved evidence and reject verification. They are never interpreted as a zero-case PASS. XML case outcomes and case counts remain per member; neither helper sums them or infers execution on ROOT from publication at ROOT.

The source-only diagnostic `/tmp/rc6_known_gzip_receipts_d9_source_only.json` reads three immutable Git members at `d9e7fb7d567ff745193c125dc9cd8df4e91ec5d6`: the fixed OWN825 receipt records 127 PASS; the original 12-case receipt records seven FAIL and five PASS; the original real-descriptor receipt records one FAIL. The parent receipt's OWN source/environment/scope is retained. This is a metadata verification with zero newly executed native cases, not proof that BIG90, capacity, runtime or final material acceptance passed.

The controls driver now has 30 small parser/ref cases, including original IDs/fields, literal ref-shaped RAW/original data, signed zero, gzip CRC/truncation/concatenation, absent gzip, altered declared hashes and a private external CLI verification. A development iteration surfaced an untyped missing-member exception; the resolver now consistently reports `RefResolutionError`. That iteration is a documentary helper/API correction, not a product RED or a new audit finding.

## ROOT named supplementary bindings

Prepare the external document with the separate standalone helper:

```sh
PYTHONDONTWRITEBYTECODE=1 python /tmp/rc6_root_supplemental_named_findings_generate.py \
  --repo /workspace/porota_rc6_convergence \
  --source FULL_STABLE_SOURCE_S_SHA \
  --out /tmp/ROOT_SUPPLEMENTAL_NAMED_FINDING_BINDINGS_SOURCE_S.json
```

The six reviewed IDs default to the typed ROOT membership category. Actual upstream exact membership must be absent; otherwise this category rejects the ID. Rebinding an existing versioned supplement follows its actual old-owner path and original workstream, preserving the ROOT/upstream distinction. Each parent RAW receipt remains verbatim; known XML/gzip members obtain separate verified publication/encoding/case metadata with their original scope.

The earlier `/tmp/ROOT_SUPPLEMENTAL_NAMED_FINDING_BINDINGS_6737de1c.json` predates the typed membership fields and compressed-member support. It is preserved as an intermediate predecessor; regenerate from the authorized stable source before integration. The future canonical file is `docs/audits/convergence/ROOT_SUPPLEMENTAL_NAMED_FINDING_BINDINGS.json`. ROOT closure must explicitly name this path and `ROOT_INTEGRATION_SUPPLEMENTAL_NAMED_FINDING_BINDINGS`; neither filename presence nor a parent owner map creates exact upstream membership.

The controlled diagnostic and final-source preparation plan are separate: `/tmp/rc6_register_derived_refs_source_only_plan.json`. All final SHA/tree/FIP/JUnit/image/runtime acceptance fields remain null until canonical final execution.
