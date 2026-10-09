"""All-prefix archive accounting, without provenance or G5 gate authority.

Every input is an upper-bound obligation, not an extrapolated closed sample.
The arithmetic can reject an incomplete proposed envelope and identify a limit
violation. It cannot authenticate its producer, source originals, filesystem,
reachable-state coverage or the supplied bounds. Those proofs remain required
by the immutable Horizon model hold. No measured seven-cut forecast is accepted
as an all1202 input, and no result from this module is a certification receipt.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import MappingProxyType

from .archive_components import MAX_COMPONENTS


HORIZON_CUTS = 1202
ARCHIVE_LIMIT_BYTES = 512 * 1024**2
MAX_DEPTH = 32
RETENTION = timedelta(hours=9 + 1)
STATE_CLASSES = frozenset({
    "admission", "build_intent", "cas_staging", "cas_published",
    "recipe_staging", "recipe_published", "receipt_staging", "checkpoint_staging",
    "ack_staging", "gc_intent", "gc_deletion", "recovery", "committed",
})
EXTRA_COST_CLASSES = frozenset({
    "control_staging", "publication_temporaries", "recovery_temporaries",
    "gc_temporaries", "filesystem_metadata", "other_owned_allocations",
})
OBJECT_KINDS = frozenset({"pack", "recipe", "receipt", "persistent_control"})


@dataclass(frozen=True)
class ObjectFootprint:
    """One whole physical object, including every unused component in a pack."""
    object_id: str
    kind: str
    logical_bytes: int
    allocated_upper_bound_bytes: int
    created_cut: int
    component_count: int = 0


@dataclass(frozen=True)
class GenerationFootprint:
    generation_id: str
    as_of: datetime
    object_ids: tuple[str, ...]
    base_generation_id: str | None
    branch: str


@dataclass(frozen=True)
class StateFootprint:
    """Simultaneous additional logical/physical costs, disjoint from objects.

    Explicit zero is an obligation to prove absence. None/UNKNOWN is rejected.
    Directories are separate physical bounds; they are not approximated using
    the allocation unit for an ordinary file. All temporary files and recovery
    materializations, including those outside the CAS directory, belong here.
    """
    directory_allocated_upper_bound_bytes: int
    extra_costs: dict[str, tuple[int, int]]


@dataclass(frozen=True)
class PrefixFootprint:
    states: dict[str, StateFootprint]
    pins: tuple[str, ...] = ()


def _number(value, reason, *, maximum=None):
    if type(value) is not int or value < 0 or maximum is not None and value > maximum:
        raise ValueError("RETENTION_PHYSICAL_UNKNOWN_OR_INVALID_" + reason)
    return value


def _identity(value, reason):
    if not isinstance(value, str) or not value or value.upper() in {"UNKNOWN", "NO_VERIFICADO"}:
        raise ValueError("RETENTION_PHYSICAL_UNKNOWN_OR_INVALID_" + reason)
    return value


def horizon_schedule(day):
    """Original arithmetic clocks only; no financial payloads or producer ticks."""
    preopen = datetime(day.year, day.month, day.day, 13, 20, tzinfo=timezone.utc)
    first = preopen.replace(hour=13, minute=35)
    return (preopen, *(first + timedelta(seconds=30 * offset) for offset in range(1201)))


def evaluate_all_prefixes(objects, generations, prefixes, *, allocation_unit):
    """Evaluate a conditional envelope, conservatively taking zero GC credit.

    A physically eligible object is not necessarily deleted. Even PREOPEN is
    retained in the accounting until authentic whole-object deletion custody
    exists; this finite conservative model needs no speculative GC savings.
    Every anchor, fallback, transitive base and shared pack remains counted.
    Controls born before the first cut use created_cut=0 (the complete A0).

    The fixed return classification and null certified bound deliberately
    prevent an untrusted arithmetic input from becoming a G5 PASS.
    """
    unit = _number(allocation_unit, "ALLOCATION_UNIT")
    if not unit:
        raise ValueError("RETENTION_PHYSICAL_UNKNOWN_OR_INVALID_ALLOCATION_UNIT")
    if len(generations) != HORIZON_CUTS or len(prefixes) != HORIZON_CUTS:
        raise ValueError("RETENTION_PHYSICAL_ALL1202_PREFIXES_REQUIRED")
    first = generations[0].as_of
    if type(first) is not datetime or first.tzinfo is None:
        raise ValueError("RETENTION_PHYSICAL_ORIGINAL_SCHEDULE_REQUIRED")
    schedule = horizon_schedule(first.date())
    known, born = {}, {cut: [] for cut in range(HORIZON_CUTS + 1)}
    for obj in objects:
        if type(obj) is not ObjectFootprint:
            raise ValueError("RETENTION_PHYSICAL_OBJECT_INVALID")
        ident = _identity(obj.object_id, "OBJECT_ID")
        if ident in known or obj.kind not in OBJECT_KINDS:
            raise ValueError("RETENTION_PHYSICAL_OBJECT_DUPLICATE_OR_KIND_INVALID")
        logical = _number(obj.logical_bytes, "OBJECT_LOGICAL_BYTES")
        physical = _number(obj.allocated_upper_bound_bytes, "OBJECT_ALLOCATED_BOUND")
        if physical % unit or physical < (logical + unit - 1) // unit * unit:
            raise ValueError("RETENTION_PHYSICAL_OBJECT_ALLOCATION_BOUND_INVALID")
        cut = _number(obj.created_cut, "OBJECT_BIRTH", maximum=HORIZON_CUTS)
        components = _number(obj.component_count, "PACK_COMPONENTS", maximum=MAX_COMPONENTS)
        if obj.kind == "pack" and not components or obj.kind != "pack" and components:
            raise ValueError("RETENTION_PHYSICAL_PACK_COMPONENT_COUNT_INVALID")
        if obj.kind != "persistent_control" and not logical:
            raise ValueError("RETENTION_PHYSICAL_ORIGINAL_OBJECT_EMPTY")
        known[ident] = obj
        born[cut].append(obj)
    by_id, depth, referenced = {}, {}, set()
    for number, (generation, clock) in enumerate(zip(generations, schedule), 1):
        if type(generation) is not GenerationFootprint or generation.as_of != clock:
            raise ValueError("RETENTION_PHYSICAL_ORIGINAL_SCHEDULE_REQUIRED")
        ident = _identity(generation.generation_id, "GENERATION_ID")
        _identity(generation.branch, "ANCHOR_OR_FALLBACK_BRANCH")
        if ident in by_id or type(generation.object_ids) is not tuple:
            raise ValueError("RETENTION_PHYSICAL_GENERATION_INVALID")
        refs = generation.object_ids
        if not refs or len(set(refs)) != len(refs):
            raise ValueError("RETENTION_PHYSICAL_GENERATION_OBJECTS_INVALID")
        if any(ref not in known or known[ref].created_cut > number for ref in refs):
            raise ValueError("RETENTION_PHYSICAL_OBJECT_MISSING_OR_FUTURE")
        kinds = [known[ref].kind for ref in refs]
        if kinds.count("recipe") != 1 or kinds.count("receipt") != 1 or "pack" not in kinds:
            raise ValueError("RETENTION_PHYSICAL_COMPLETE_PACK_AND_METADATA_REQUIRED")
        if any(known[ref].created_cut != number for ref in refs
               if known[ref].kind in {"recipe", "receipt"}):
            raise ValueError("RETENTION_PHYSICAL_ORIGINAL_METADATA_BIRTH_INVALID")
        base = generation.base_generation_id
        if base is not None and base not in by_id:
            raise ValueError("RETENTION_PHYSICAL_BASE_MISSING_CYCLE_OR_FUTURE")
        depth[ident] = depth[base] + 1 if base is not None else 0
        if depth[ident] > MAX_DEPTH:
            raise ValueError("RETENTION_PHYSICAL_DEPENDENCY_DEPTH_EXCEEDED")
        by_id[ident] = generation
        referenced.update(refs)
    if any(obj.kind != "persistent_control" and obj.object_id not in referenced for obj in objects):
        # Recovery/uncommitted objects also consume bytes, but their custody
        # and lifetime belong in the explicit simultaneous additional bounds.
        raise ValueError("RETENTION_PHYSICAL_UNEXPLAINED_PERSISTENT_OBJECT")
    physical = sum(obj.allocated_upper_bound_bytes for obj in born[0])
    logical = sum(obj.logical_bytes for obj in born[0])
    component_count = sum(obj.component_count for obj in born[0])
    present, rows = {}, []
    for number, (generation, prefix) in enumerate(zip(generations, prefixes), 1):
        if (type(prefix) is not PrefixFootprint or type(prefix.states) is not dict
                or set(prefix.states) != STATE_CLASSES or type(prefix.pins) is not tuple):
            raise ValueError("RETENTION_PHYSICAL_ALL_REACHABLE_STATES_REQUIRED")
        present[generation.generation_id] = generation
        if len(set(prefix.pins)) != len(prefix.pins) or any(pin not in present for pin in prefix.pins):
            raise ValueError("RETENTION_PHYSICAL_RECOVERY_PIN_INVALID")
        physical += sum(obj.allocated_upper_bound_bytes for obj in born[number])
        logical += sum(obj.logical_bytes for obj in born[number])
        component_count += sum(obj.component_count for obj in born[number])
        if component_count > MAX_COMPONENTS:
            raise ValueError("RETENTION_PHYSICAL_GLOBAL_CATALOG_CAPACITY_EXCEEDED")
        cutoff = generation.as_of - RETENTION
        live = {ident for ident, cut in present.items() if cut.as_of >= cutoff}
        live.update(prefix.pins)
        for ident in tuple(live):
            base = present[ident].base_generation_id
            while base is not None:
                live.add(base)
                base = present[base].base_generation_id
        # The original GC can expire only a contiguous prefix and always
        # preserves the receipt head. Eligibility never implies reclaimed bytes.
        eligible = []
        for ident in present:
            if ident in live or ident == generation.generation_id:
                break
            eligible.append(ident)
        kept_packs = {ref for ident, cut in present.items() if ident not in eligible
                      for ref in cut.object_ids if known[ref].kind == "pack"}
        eligible_packs = {ref for ident in eligible for ref in present[ident].object_ids
                          if known[ref].kind == "pack"} - kept_packs
        states = {}
        for name, state in prefix.states.items():
            if (type(state) is not StateFootprint or type(state.extra_costs) is not dict
                    or set(state.extra_costs) != EXTRA_COST_CLASSES):
                raise ValueError("RETENTION_PHYSICAL_COMPLETE_TEMP_RECOVERY_METADATA_BOUND_REQUIRED")
            directory = _number(state.directory_allocated_upper_bound_bytes, "DIRECTORY_BOUND")
            if directory % unit:
                raise ValueError("RETENTION_PHYSICAL_DIRECTORY_ALLOCATION_INVALID")
            extra_logical = extra_physical = 0
            for kind, cost in state.extra_costs.items():
                if type(cost) is not tuple or len(cost) != 2:
                    raise ValueError("RETENTION_PHYSICAL_UNKNOWN_OR_INVALID_" + kind.upper())
                extra_logical += _number(cost[0], kind.upper() + "_LOGICAL")
                extra_physical += _number(cost[1], kind.upper() + "_PHYSICAL")
                if cost[1] % unit or cost[1] < (cost[0] + unit - 1) // unit * unit:
                    raise ValueError("RETENTION_PHYSICAL_ADDITIONAL_ALLOCATION_BOUND_INVALID")
            states[name] = {"logical_upper_bound_bytes": logical + extra_logical,
                            "physical_upper_bound_bytes": physical + directory + extra_physical}
        rows.append({"cut": number, "as_of": generation.as_of.isoformat(),
                     "states": states, "catalog_components_upper_bound": component_count,
                     "retained_generation_count": len(live),
                     "eligible_generation_ids": tuple(eligible),
                     "eligible_whole_pack_ids": tuple(sorted(eligible_packs)), "gc_credit_bytes": 0})
    return MappingProxyType({
        "classification": "CONDITIONAL_ARITHMETIC_ONLY_NOT_G5_CERTIFICATION",
        "global_complete_physical_upper_bound_bytes": None,
        "conditional_all1202_physical_upper_bound_bytes": max(
            state["physical_upper_bound_bytes"] for row in rows for state in row["states"].values()),
        "conditional_all1202_logical_upper_bound_bytes": max(
            state["logical_upper_bound_bytes"] for row in rows for state in row["states"].values()),
        "original_limit_bytes": ARCHIVE_LIMIT_BYTES,
        "prefixes": tuple(rows), "gc_credit_bytes": 0,
        "missing_certification": (
            "AUTHENTIC_ORIGINAL_ALL1202_MEMBER_AND_FALLBACK_ENVELOPES",
            "AUTHENTIC_COMPLETE_COST_GRAPH_AND_REACHABLE_STATE_PROVENANCE",
            "NATIVE_FILESYSTEM_DIRECTORY_AND_SIMULTANEOUS_RECOVERY_BOUND",
            "ALL_ORIGINAL_BYTES_CRC_SHA256_AND_DEPTH_RESTORATION_PROOF",
        ),
    })
