"""Cheap all1202 arithmetic counterexamples; no Horizon producer authority."""
from dataclasses import replace
from datetime import date, timedelta
import hashlib

import pytest

from rc6_shadow_runtime import archive_physical_model as model


def envelope(*, preopen_has_private_pack=False):
    clocks = model.horizon_schedule(date(2026, 10, 9))
    objects = [model.ObjectFootprint("shared-pack", "pack", 1000000, 1003520, 1, 100)]
    if preopen_has_private_pack:
        objects.append(model.ObjectFootprint("preopen-pack", "pack", 1000000, 1003520, 1, 100))
    generations = []
    state = model.StateFootprint(4096, {name: (0, 0) for name in model.EXTRA_COST_CLASSES})
    prefixes = [model.PrefixFootprint({name: state for name in model.STATE_CLASSES}) for _ in clocks]
    for number, clock in enumerate(clocks, 1):
        recipe, receipt = "recipe-" + str(number), "receipt-" + str(number)
        objects.extend((model.ObjectFootprint(recipe, "recipe", 500, 4096, number),
                        model.ObjectFootprint(receipt, "receipt", 500, 4096, number)))
        pack = "preopen-pack" if number == 1 and preopen_has_private_pack else "shared-pack"
        generations.append(model.GenerationFootprint(f"{number:032x}", clock,
            (pack, recipe, receipt), None, "ORIGINAL_FULL_ANCHOR_OR_FALLBACK"))
    return objects, generations, prefixes


def evaluate(value):
    return model.evaluate_all_prefixes(*value, allocation_unit=4096)


def test_original_schedule_includes_preopen_both_closed_edges_and_strict_expiry():
    clocks = model.horizon_schedule(date(2026, 10, 9))
    assert len(clocks) == 1202
    assert clocks[0].isoformat() == "2026-10-09T13:20:00+00:00"
    assert clocks[1].isoformat() == "2026-10-09T13:35:00+00:00"
    assert clocks[770].isoformat() == "2026-10-09T19:59:30+00:00"
    assert clocks[771].isoformat() == "2026-10-09T20:00:00+00:00"
    assert clocks[772].isoformat() == "2026-10-09T20:00:30+00:00"
    assert clocks[-1].isoformat() == "2026-10-09T23:35:00+00:00"
    assert sum(clock.hour < 20 for clock in clocks[1:]) == 770
    assert sum(clock.hour >= 20 for clock in clocks[1:]) == 431
    report = evaluate(envelope())
    equality = next(row for row in report["prefixes"] if "23:20:00" in row["as_of"])
    after = next(row for row in report["prefixes"] if "23:20:30" in row["as_of"])
    assert equality["eligible_generation_ids"] == ()
    assert after["eligible_generation_ids"] == (f"{1:032x}",)
    assert report["prefixes"][-1]["retained_generation_count"] == 1201
    assert report["gc_credit_bytes"] == 0


def test_shared_pack_is_counted_whole_once_and_cannot_receive_gc_credit_for_expired_recipe():
    objects, generations, prefixes = envelope()
    report = evaluate((objects, generations, prefixes))
    final = report["prefixes"][-1]
    expected = 1003520 + 1202 * 2 * 4096 + 4096
    assert report["conditional_all1202_physical_upper_bound_bytes"] == expected
    assert report["conditional_all1202_logical_upper_bound_bytes"] == 1000000 + 1202 * 2 * 500
    assert final["eligible_generation_ids"] == (f"{1:032x}",)
    assert final["eligible_whole_pack_ids"] == ()
    assert final["catalog_components_upper_bound"] == 100
    assert final["gc_credit_bytes"] == 0


def test_an_eligible_unshared_preopen_pack_is_still_not_a_physical_deletion_receipt():
    report = evaluate(envelope(preopen_has_private_pack=True))
    assert report["prefixes"][-1]["eligible_whole_pack_ids"] == ("preopen-pack",)
    assert report["conditional_all1202_physical_upper_bound_bytes"] == 2 * 1003520 + 1202 * 2 * 4096 + 4096
    assert report["gc_credit_bytes"] == 0


def test_recovery_pin_and_original_transitive_base_keep_expired_anchor_reachable():
    objects, generations, prefixes = envelope(preopen_has_private_pack=True)
    generations[1] = replace(generations[1], base_generation_id=generations[0].generation_id,
                             branch="ORIGINAL_DEPENDENT_MEMBER")
    report = evaluate((objects, generations, prefixes))
    assert report["prefixes"][-1]["eligible_generation_ids"] == ()
    assert report["prefixes"][-1]["retained_generation_count"] == 1202
    generations[1] = replace(generations[1], base_generation_id=None)
    prefixes[-1] = replace(prefixes[-1], pins=(generations[0].generation_id,))
    report = evaluate((objects, generations, prefixes))
    assert report["prefixes"][-1]["eligible_generation_ids"] == ()


def test_peak_can_be_an_early_recovery_state_even_when_the_committed_final_state_fits():
    objects, generations, prefixes = envelope()
    selected = prefixes[14]
    states = dict(selected.states)
    recovery = states["recovery"]
    costs = dict(recovery.extra_costs)
    costs["recovery_temporaries"] = (513 * 1024**2, 513 * 1024**2)
    states["recovery"] = replace(recovery, extra_costs=costs)
    prefixes[14] = replace(selected, states=states)
    report = evaluate((objects, generations, prefixes))
    assert report["prefixes"][-1]["states"]["committed"]["physical_upper_bound_bytes"] < model.ARCHIVE_LIMIT_BYTES
    assert report["conditional_all1202_physical_upper_bound_bytes"] > model.ARCHIVE_LIMIT_BYTES
    assert report["conditional_all1202_physical_upper_bound_bytes"] == report["prefixes"][14]["states"]["recovery"]["physical_upper_bound_bytes"]
    assert report["global_complete_physical_upper_bound_bytes"] is None


@pytest.mark.parametrize("length", [0, 7, 1049, 1201])
def test_partial_forecasts_and_original_failed_prefixes_are_not_all1202_bounds(length):
    objects, generations, prefixes = envelope()
    with pytest.raises(ValueError, match="ALL1202_PREFIXES_REQUIRED"):
        evaluate((objects, generations[:length], prefixes[:length]))


@pytest.mark.parametrize("missing", sorted(model.EXTRA_COST_CLASSES))
def test_missing_metadata_temp_recovery_or_other_allocation_is_fail_closed(missing):
    objects, generations, prefixes = envelope()
    states = dict(prefixes[7].states)
    costs = dict(states["cas_staging"].extra_costs)
    del costs[missing]
    states["cas_staging"] = replace(states["cas_staging"], extra_costs=costs)
    prefixes[7] = replace(prefixes[7], states=states)
    with pytest.raises(ValueError, match="COMPLETE_TEMP_RECOVERY_METADATA_BOUND_REQUIRED"):
        evaluate((objects, generations, prefixes))


@pytest.mark.parametrize("unknown", [None, True, "UNKNOWN", -1, 1.5])
def test_directory_physical_cost_never_inherits_an_ordinary_file_roundup(unknown):
    objects, generations, prefixes = envelope()
    states = dict(prefixes[0].states)
    states["recovery"] = replace(states["recovery"], directory_allocated_upper_bound_bytes=unknown)
    prefixes[0] = replace(prefixes[0], states=states)
    with pytest.raises(ValueError, match="UNKNOWN_OR_INVALID_DIRECTORY_BOUND"):
        evaluate((objects, generations, prefixes))


@pytest.mark.parametrize("attack", ["missing_state", "extra_state", "skipped_close", "future_base", "depth33", "missing_pack", "future_object", "unexplained_object", "catalog_overflow", "unknown_branch"])
def test_complete_original_schedule_graph_catalog_and_all_states_remain_mandatory(attack):
    objects, generations, prefixes = envelope()
    if attack == "missing_state":
        states = dict(prefixes[0].states)
        del states["recovery"]
        prefixes[0] = replace(prefixes[0], states=states)
    elif attack == "extra_state":
        prefixes[0] = replace(prefixes[0], states={**prefixes[0].states, "UNMODELED": prefixes[0].states["recovery"]})
    elif attack == "skipped_close":
        generations[771] = replace(generations[771], as_of=generations[771].as_of + timedelta(seconds=30))
    elif attack == "future_base":
        generations[0] = replace(generations[0], base_generation_id=generations[1].generation_id)
    elif attack == "depth33":
        for number in range(1, 34):
            generations[number] = replace(generations[number], base_generation_id=generations[number-1].generation_id)
    elif attack == "missing_pack":
        generations[0] = replace(generations[0], object_ids=generations[0].object_ids[1:])
    elif attack == "future_object":
        objects[0] = replace(objects[0], created_cut=2)
    elif attack == "unexplained_object":
        objects.append(model.ObjectFootprint("unexplained-pack", "pack", 100, 4096, 5, 1))
    elif attack == "catalog_overflow":
        objects[0] = replace(objects[0], component_count=model.MAX_COMPONENTS)
        objects.append(model.ObjectFootprint("additional-pack", "pack", 100, 4096, 1, 1))
        generations[0] = replace(generations[0], object_ids=generations[0].object_ids + ("additional-pack",))
    elif attack == "unknown_branch":
        generations[0] = replace(generations[0], branch="NO_VERIFICADO")
    with pytest.raises(ValueError, match="RETENTION_PHYSICAL_"):
        evaluate((objects, generations, prefixes))


def test_arithmetic_envelope_cannot_claim_authentic_provenance_or_a_g5_certification():
    report = evaluate(envelope())
    assert report["classification"] == "CONDITIONAL_ARITHMETIC_ONLY_NOT_G5_CERTIFICATION"
    assert report["global_complete_physical_upper_bound_bytes"] is None
    assert report["missing_certification"]
    with pytest.raises(TypeError):
        report["global_complete_physical_upper_bound_bytes"] = 1


def original_metadata_prefix(count=2):
    """Unit metadata only: no original financial bytes or artifact authority."""
    clocks = model.horizon_schedule(date(2026, 10, 5))
    rows = []
    for index, clock in enumerate(clocks[:count]):
        hashes = {name: hashlib.sha256((str(index) + name).encode()).hexdigest() for name in model.MEMBERS}
        rows.append({"tick_index": index, "as_of": clock.isoformat(), "sequence": index + 1,
            "phase": "PREOPEN" if index == 0 else "OPEN" if index <= 770 else "CLOSED",
            "generation_id": f"{index + 1:032x}", "member_bytes": {name: 1 for name in model.MEMBERS},
            "original_member_sha256": hashes, "original_manifest_sha256": hashes["manifest.json"],
            "original_manifest_clock": clock.isoformat(), "original_generation_custody_unchanged": True,
            "real_orders_sent": 0, "real_routes": "NOT_CALLED", "recipe_bytes": 1,
            "archive_residence": {"allocated_bytes_including_directories": 4096 * (index + 1),
                "logical_bytes_including_directories": index + 1, "pending_temporaries_or_intents": 0},
            "verified_native_archive_head": {"contracted_horizon_seconds": 32400, "recovery_margin_seconds": 3600}})
    return rows


def test_original_prefix_guard_preserves_missing_payloads_and_does_not_extrapolate_closed_samples():
    cuts = original_metadata_prefix(773)
    report = model.original_prefix_obligations(cuts)
    assert report["captured_original_cuts"] == 773 and len(report["missing_original_cut_indices"]) == 429
    assert report["cuts_with_all_five_payloads_available"] == 0 and report["known_cuts_missing_original_payloads"] == 773
    assert report["phase_counts"] == {"PREOPEN": 1, "OPEN": 770, "CLOSED": 2}
    assert [(row["tick_index"], row["phase"]) for row in report["measured_edges_and_last_cut"]][-3:] == [
        (770, "OPEN"), (771, "CLOSED"), (772, "CLOSED")]
    assert report["observation_is_continuous_peak"] is report["projected_missing_cuts"] is False
    assert report["global_complete_physical_upper_bound_bytes"] is None and report["g5_equivalence_demonstrated"] is False
    with pytest.raises(ValueError, match="ALL1202_AUTHENTIC_ORIGINAL_BYTES_REQUIRED"):
        model.require_original_bytes_before_all1202_model(report)


def test_all1202_hashes_alone_cannot_close_original_byte_coverage_or_model():
    cuts = original_metadata_prefix(1202)
    report = model.original_prefix_obligations(cuts)
    assert report["missing_original_cut_indices"] == () and report["all1202_original_bytes_coverage"] is False
    with pytest.raises(ValueError, match="ALL1202_AUTHENTIC_ORIGINAL_BYTES_REQUIRED"):
        model.require_original_bytes_before_all1202_model(report)
    payloads = {value for cut in cuts for value in cut["original_member_sha256"].values()}
    availability_only = model.original_prefix_obligations(cuts, available_payload_sha256=payloads)
    assert model.require_original_bytes_before_all1202_model(availability_only) == availability_only
    assert availability_only["original_anchor_and_fallback_layouts_verified"] is False
    assert availability_only["all_gc_recovery_temp_states_verified"] is False
    assert availability_only["global_complete_physical_upper_bound_bytes"] is None


@pytest.mark.parametrize("attack", ("phase", "clock", "member_hashes", "member_size", "retention",
    "pending_temporary", "source_custody", "duplicate_generation", "orders"))
def test_original_model_guard_rejects_changed_clock_closed_or_retention_contract(attack):
    cuts = original_metadata_prefix(773)
    victim = cuts[772]
    if attack == "phase": victim["phase"] = "OPEN"
    elif attack == "clock": victim["as_of"] = cuts[771]["as_of"]
    elif attack == "member_hashes": victim["original_member_sha256"].pop("projection.sqlite")
    elif attack == "member_size": victim["member_bytes"]["projection.sqlite"] = True
    elif attack == "retention": victim["verified_native_archive_head"]["recovery_margin_seconds"] = 0
    elif attack == "pending_temporary": victim["archive_residence"]["pending_temporaries_or_intents"] = 1
    elif attack == "source_custody": victim["original_generation_custody_unchanged"] = False
    elif attack == "duplicate_generation": victim["generation_id"] = cuts[771]["generation_id"]
    elif attack == "orders": victim["real_orders_sent"] = 1
    with pytest.raises(ValueError, match="RETENTION_PHYSICAL_"):
        model.original_prefix_obligations(cuts)
