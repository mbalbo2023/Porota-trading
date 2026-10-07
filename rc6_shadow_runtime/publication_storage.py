"""Private, bounded capture reuse within one native evidence publication.

This is an encoder optimization, never a verification or admission authority.
The public prepared encoder and externally supplied caches retain their original
path. Every role counts its own graph and hashes every expanded occurrence.
"""
from decimal import Decimal
from contextvars import ContextVar
from collections import Counter
import sys
from types import FunctionType, GetSetDescriptorType

from . import packed_storage as packed


# Exhaustion disables reuse; it never rejects an otherwise accepted payload.
MAX_SNAPSHOT_CONTAINERS = 262144
MAX_SNAPSHOT_REFERENCES = 8000000
MAX_SNAPSHOT_BYTES = 128 * 1024**2
MAX_CACHE_DEPENDENCIES = 262144
MIN_FRAME_ITEMS = 64
MAX_FRAME_PLANS = 4096
MAX_FRAME_PREFIX_BYTES = 65536
MAX_ROOT_CANDIDATES = 4096
MAX_ROLE_CONTAINERS = 1048576
MAX_ROLE_REFERENCES = 16000000
_LEAVES = (type(None), bool, int, float, str)
_CONTAINERS = (dict, list, tuple)
_ROLE_TYPES = (*_CONTAINERS, *_LEAVES, Decimal)
_COUNTER_METHODS = ("__getattribute__", "__iter__", "__len__", "__getitem__", "keys", "values", "items", "get")
_COUNTER_DICTIONARY_DESCRIPTOR = type.__getattribute__(Counter, "__dict__").get("__dict__")


def _plain_counter(node):
    # Lookup through the class can execute a newly installed descriptor. Read
    # namespace/MRO entries statically before touching this exact Counter.
    mro = type.__getattribute__(Counter, "__mro__")
    namespace = type.__getattribute__(Counter, "__dict__")
    descriptor = _COUNTER_DICTIONARY_DESCRIPTOR
    if (type(descriptor) is not GetSetDescriptorType or descriptor.__objclass__ is not Counter
            or descriptor.__name__ != "__dict__"
            or len(mro) != 3 or any(current is not expected for current, expected in zip(mro, (Counter, dict, object)))
            or any(name in namespace for name in _COUNTER_METHODS)
            or "__getattr__" in namespace
            or namespace.get("__dict__") is not _COUNTER_DICTIONARY_DESCRIPTOR):
        return False
    return not object.__getattribute__(node, "__dict__")


def _constants():
    result = (packed.PACK_TARGET, packed.MAX_BINDINGS, packed._FIELDS,
            packed._SMALL_ITEMS, packed._SMALL_STRING, packed._SMALL_INTEGER_BITS,
            packed._BINDING_SCALAR_ENTRIES, packed._BINDING_SCALAR_BYTES,
            packed._BINDING_SCALAR_STRING, packed._BINDING_SCALAR_INTEGER_BITS, MIN_FRAME_ITEMS)
    if (type(result[2]) is not frozenset or any(type(name) is not str for name in result[2])
            or any(type(value) is not int for i, value in enumerate(result) if i != 2)):
        return None
    builder_namespace = type.__getattribute__(packed._CaptureBuilder, "__dict__")
    buffer_namespace = type.__getattribute__(packed._CaptureBuffer, "__dict__")
    operations = (packed._canonical, packed._root_volatile, packed._small_plain,
        *(builder_namespace.get(name) for name in ("_scalar", "_binding_scalar", "_named", "_append")),
        *(buffer_namespace.get(name) for name in ("append", "bind", "boundary", "flush", "_room")))
    if (type(packed._VOLATILE) is not type(packed._HEX)
            or any(type(operation) is not FunctionType for operation in operations)):
        return None
    return (*result, packed._VOLATILE, *((operation, operation.__code__) for operation in operations))


# Diagnostic state has no admission or replay authority. One fixed-size
# vector is emitted after each observed real constructor, never per data node.
_REPLAY_REASON_CODES = ('ROLE_ATTEMPT', 'ROLE_ROOT_KIND', 'ROLE_NO_CANDIDATE', 'ROLE_CONTAINER_CAP', 'ROLE_COUNTER_AUTH', 'ROLE_NODE_KIND', 'ROLE_REFERENCE_CAP', 'ROLE_KEY_KIND', 'ROLE_CHILD_KIND', 'ROLE_ACCEPTED', 'ROLE_ACCEPTED_COUNTER_PRESENT', 'SNAPSHOT_ATTEMPT', 'SNAPSHOT_ROOT_KIND', 'SNAPSHOT_PREFLIGHT_CAPS', 'SNAPSHOT_KEY_KIND', 'SNAPSHOT_QUEUED_CAP', 'SNAPSHOT_CHILD_KIND', 'SNAPSHOT_BUILDER_IDENTITY', 'SNAPSHOT_MEMORY_CAP', 'SNAPSHOT_VALID', 'MATCH_ATTEMPT', 'MATCH_NODE_ALIAS_LENGTH', 'MATCH_KEY_IDENTITY', 'MATCH_CHILD_IDENTITY', 'MATCH_VALID', 'FRAME_ATTEMPT', 'PLAN_DISABLED_KEY', 'PLAN_FOUND', 'REPLAY_USED', 'REPLAY_DEPENDENCY_CHANGED', 'REPLAY_CHAIN_REJECTED', 'PLAN_NEW', 'PLAN_COUNT_CAP', 'DISABLED_COUNT_CAP', 'CONSTANTS_INVALID', 'FRAME_SNAPSHOT_REJECTED', 'PLAN_CAPTURE_ENTER', 'PLAN_CAPTURE_RETURN', 'PLAN_POST_CAPTURE_REJECTED', 'PLAN_BYTES_CAP', 'PLAN_STORED')
_REPLAY_MAX_COUNTER = 2**63 - 1
_PUBLICATION_REPLAY_OBSERVATION = ContextVar("rc6_publication_replay_observation", default=None)
_PUBLICATION_REPLAY_HEALTH = ContextVar("rc6_publication_replay_health", default=None)


def _fail_replay_shared(shared):
    """Only a native metadata dictionary may receive the irreversible veto."""
    try:
        if type(shared) is dict and len(shared) <= 3 and all(type(key) is str for key in shared):
            shared["healthy"] = False
    except BaseException:
        pass  # No formatting/getters or business-exception replacement.


def _replay_shared_snapshot(shared):
    """Copy only native typed metadata, without invoking a foreign getter."""
    try:
        if type(shared) is not dict or len(shared) != 3:
            return None
        snapshot = shared.copy()
        fields = ("healthy", "constructor_closes", "report_puts_returned")
        if (len(snapshot) != len(fields)
                or any(type(key) is not str or key not in fields for key in snapshot)):
            return None
        healthy, closed, returned = (snapshot[key] for key in fields)
        if (type(healthy) is not bool or type(closed) is not int or type(returned) is not int
                or not 0 <= returned <= closed <= _REPLAY_MAX_COUNTER):
            return None
        return snapshot
    except BaseException:
        return None


class _ReplayObservation:
    __slots__ = ("shared", "counts", "fault", "closed")

    def __init__(self, shared):
        self.shared = shared
        self.counts = dict.fromkeys(_REPLAY_REASON_CODES, 0)
        self.fault = False
        self.closed = False

    def fail(self):
        self.fault = True
        _fail_replay_shared(self.shared)

    def note(self, reason):
        try:
            if type(self.closed) is not bool or type(self.fault) is not bool:
                self.fail()
                return
            if self.closed or self.fault:
                return
            current = self.counts[reason]
            if type(current) is not int or not 0 <= current < _REPLAY_MAX_COUNTER:
                self.fail()
                return
            self.counts[reason] = current + 1
        except BaseException:
            self.fail()

    def finish(self, constructor_completed, context_reset_returned):
        self.closed = True
        try:
            fault = self.fault
            if (type(fault) is not bool or type(self.counts) is not dict
                    or len(self.counts) != len(_REPLAY_REASON_CODES)
                    or type(constructor_completed) is not bool or type(context_reset_returned) is not bool):
                self.fail()
                return None
            counts = self.counts.copy()
            if (len(counts) != len(_REPLAY_REASON_CODES)
                    or any(type(key) is not str or key not in _REPLAY_REASON_CODES for key in counts)
                    or any(type(value) is not int or not 0 <= value <= _REPLAY_MAX_COUNTER
                           for value in counts.values())):
                self.fail()
                return None
            shared = _replay_shared_snapshot(self.shared)
            if shared is None:
                self.fail()
                return None
            healthy = (not fault and shared["healthy"] is True and context_reset_returned is True)
            return {"schema": "rc6.publication-replay-counters.v1",
                "observation_context_closed": context_reset_returned,
                "context_reset_returned": context_reset_returned,
                "constructor_completed": constructor_completed,
                "observational_fault": fault,
                "observational_health": healthy,
                "counters_complete": healthy,
                "counter_maximum": _REPLAY_MAX_COUNTER,
                "counters": counts,
                "admission_or_replay_authority": False,
                "exclusive_cost_or_material_GREEN_inferred": False}
        except BaseException:
            self.fail()
            return None


def _note_replay(reason):
    health = _PUBLICATION_REPLAY_HEALTH.get()
    if health is None:
        return
    try:
        observation = _PUBLICATION_REPLAY_OBSERVATION.get()
        if type(observation) is not _ReplayObservation:
            raise ValueError("PUBLICATION_REPLAY_OBSERVER_REGISTRATION_INVALID")
        observation.note(reason)
    except BaseException:
        # No reporting/classification may run before the independent fault bit.
        _fail_replay_shared(health)


class _Snapshot:
    """Strong edge references, with no equality or callbacks from input data."""
    def __init__(self, builder, root, budget):
        _note_replay("SNAPSHOT_ATTEMPT")
        self.nodes = {}
        self.bytes = self.references = 0
        self.valid = False
        pending = [root]
        queued = {id(root)}
        while pending:
            node = pending.pop()
            kind = type(node)
            if kind is not dict and kind is not list and kind is not tuple:
                _note_replay('SNAPSHOT_ROOT_KIND')
                return
            key = id(node)
            if key in self.nodes:
                continue
            count = len(node)
            refs = count * (2 if kind is dict else 1)
            if (budget[0] + len(self.nodes) + 1 > MAX_SNAPSHOT_CONTAINERS
                    or budget[1] + self.references + refs > MAX_SNAPSHOT_REFERENCES
                    or budget[2] + self.bytes + refs * 8 + 256 > MAX_SNAPSHOT_BYTES):
                _note_replay('SNAPSHOT_PREFLIGHT_CAPS')
                return
            keys = tuple(node) if kind is dict else ()
            if any(type(name) is not str for name in keys):
                _note_replay('SNAPSHOT_KEY_KIND')
                return
            children = tuple(node.values()) if kind is dict else tuple(node)
            for child in children:
                child_kind = type(child)
                if child_kind is dict or child_kind is list or child_kind is tuple:
                    child_key = id(child)
                    if child_key not in queued:
                        if budget[0] + len(queued) + 1 > MAX_SNAPSHOT_CONTAINERS:
                            _note_replay('SNAPSHOT_QUEUED_CAP')
                            return
                        queued.add(child_key); pending.append(child)
                elif (child_kind is not type(None) and child_kind is not bool
                      and child_kind is not int and child_kind is not float and child_kind is not str):
                    _note_replay('SNAPSHOT_CHILD_KIND')
                    return
            if builder.objects.get(key) is not node:
                _note_replay('SNAPSHOT_BUILDER_IDENTITY')
                return
            entry = (node, keys, children, builder.incoming.get(key, 0) >= 2)
            before = sys.getsizeof(self.nodes)
            self.nodes[key] = entry
            self.bytes += (sys.getsizeof(self.nodes) - before + sys.getsizeof(key)
                           + sys.getsizeof(entry) + sys.getsizeof(keys) + sys.getsizeof(children))
            self.references += refs
            if (budget[2] + self.bytes + sys.getsizeof(pending) + sys.getsizeof(queued)
                    + len(queued) * sys.getsizeof(key) > MAX_SNAPSHOT_BYTES):
                _note_replay('SNAPSHOT_MEMORY_CAP')
                return
        self.valid = True
        _note_replay("SNAPSHOT_VALID")

    def matches(self, builder):
        _note_replay("MATCH_ATTEMPT")
        for key, (node, keys, children, aliased) in self.nodes.items():
            if (builder.objects.get(key) is not node
                    or (builder.incoming.get(key, 0) >= 2) is not aliased
                    or len(node) != len(children)):
                _note_replay('MATCH_NODE_ALIAS_LENGTH')
                return False
            if type(node) is dict:
                if any(current is not prior for current, prior in zip(node, keys)):
                    _note_replay('MATCH_KEY_IDENTITY')
                    return False
                values = node.values()
            else:
                values = node
            if any(current is not prior for current, prior in zip(values, children)):
                _note_replay('MATCH_CHILD_IDENTITY')
                return False
        _note_replay('MATCH_VALID')
        return True


class _Dependencies:
    def __init__(self):
        self.keys, self.before = [], []
        self.valid = True

    def add(self, key, result):
        if not self.valid:
            return
        if type(key) is not int or len(self.keys) >= MAX_CACHE_DEPENDENCIES:
            self.valid = False
            self.keys.clear(); self.before.clear()
            return
        self.keys.append(key); self.before.append(result)

    def finish(self, cache, snapshot):
        if not self.valid:
            return None
        selected = tuple(dict.get(cache, key) for key in self.keys)
        for key, before, after in zip(self.keys, self.before, selected):
            if (type(after) is not tuple or len(after) != 2
                    or snapshot.nodes.get(key, (None,))[0] is not after[0]
                    or type(after[1]) is not packed.Capture
                    or type(after[1].template) is not bytes
                    or type(after[1].literals) is not tuple
                    or any(type(raw) is not bytes for raw in after[1].literals)):
                return None
            if before is not None and before is not after:
                return None
            # A fresh short/no-literal shortcut must be precisely the same
            # builtin canonical bytes that a later cache hit re-encodes.
            if (before is None and len(after[1].template) < 64
                    and not after[1].literals
                    and after[1].template != packed._canonical(after[0])):
                return None
        return tuple(self.keys), selected


class _SectionScope:
    def __init__(self, values, mutable):
        self.candidates = set()
        occurrences = {}
        if type(values) is dict:
            for value in values.values():
                if type(value) is not dict:
                    continue
                for name, member in value.items():
                    if (type(name) is str and name not in mutable
                            and (type(member) is dict or type(member) is list or type(member) is tuple)):
                        key = id(member), name
                        if key not in occurrences and len(occurrences) >= MAX_ROOT_CANDIDATES:
                            occurrences.clear()
                            break
                        occurrences[key] = occurrences.get(key, 0) + 1
                else:
                    continue
                break
            self.candidates = {key for key, count in occurrences.items() if count >= 2}
        self.plans, self.disabled = {}, set()
        self.budget = [0, 0, 0]
        self.busy = False
        self.closed = False
        self.exhausted = False
        self.active_root = None
        self.trace = None
        self.minimum_items = MIN_FRAME_ITEMS

    def eligible_role(self, builder, value):
        # Replay skips private scalar/binding cache effects. No callback may
        # observe them later in this role, including another root section.
        # Exact Decimal is allowed here only; snapshots still exclude it and
        # its original default=str serialization is never replayed.
        _note_replay("ROLE_ATTEMPT")
        counter_seen = False
        if type(value) is not dict:
            _note_replay('ROLE_ROOT_KIND')
            return False
        # With no activable root capture, replay cannot occur in this role.
        # Candidates already exclude the publisher's mutable root fields.
        # Exact strings and builtin dict iteration add no user comparisons.
        if not any(type(name) is str and (id(member), name) in self.candidates
                   for name, member in dict.items(value)):
            _note_replay('ROLE_NO_CANDIDATE')
            return False
        if len(builder.objects) > MAX_ROLE_CONTAINERS:
            _note_replay('ROLE_CONTAINER_CAP')
            return False
        references = 0
        for node in builder.objects.values():
            kind = type(node)
            counter = kind is Counter
            if counter:
                counter_seen = True
                if not _plain_counter(node):
                    _note_replay('ROLE_COUNTER_AUTH')
                    return False
            elif kind is not dict and kind is not list and kind is not tuple:
                _note_replay('ROLE_NODE_KIND')
                return False
            references += len(node) * (2 if kind is dict or counter else 1)
            if references > MAX_ROLE_REFERENCES:
                _note_replay('ROLE_REFERENCE_CAP')
                return False
            if kind is dict or counter:
                if any(type(key) is not str for key in node):
                    _note_replay('ROLE_KEY_KIND')
                    return False
                children = node.values()
            else:
                children = node
            for child in children:
                child_kind = type(child)
                if (child_kind is not dict and child_kind is not list and child_kind is not tuple
                        and child_kind is not type(None) and child_kind is not bool
                        and child_kind is not int and child_kind is not float and child_kind is not str
                        and child_kind is not Decimal and child_kind is not Counter):
                    _note_replay('ROLE_CHILD_KIND')
                    return False
        _note_replay('ROLE_ACCEPTED')
        if counter_seen:
            _note_replay("ROLE_ACCEPTED_COUNTER_PRESENT")
        return True

    def record_dependency(self, key, result):
        if self.trace is not None:
            self.trace.add(key, result)

    def capture(self, builder, value, name):
        if self.closed:
            return builder._capture_original(value, name)
        before = self.active_root
        # A nested public capture/default=str callback cannot borrow the
        # surrounding root's private eligibility, even in the same thread.
        key = (id(value), name) if type(name) is str else None
        self.active_root = (key if before is None and not self.busy
                            and key in self.candidates else None)
        try:
            return builder._capture_original(value, name)
        finally:
            self.active_root = before

    def append(self, builder, value, name, buffer, *, root):
        if (self.closed or self.exhausted or self.busy or self.active_root is None or type(name) is not str
                or type(buffer) is not packed._CaptureBuffer
                or type(buffer.template) is not bytearray or type(buffer.literals) is not list
                or type(buffer.result) is not list or any(type(raw) is not bytes for raw in buffer.literals)
                or len(buffer.template) > MAX_FRAME_PREFIX_BYTES):
            return False
        _note_replay('FRAME_ATTEMPT')
        key = id(value), name, root, self.active_root
        if key in self.disabled:
            _note_replay('PLAN_DISABLED_KEY')
            return False
        self.busy = True
        try:
            previous = self.plans.get(key)
            if previous is not None:
                _note_replay('PLAN_FOUND')
                snapshot, constants, dependencies, prefix, literals, suffix, tail, tail_literals = previous
                if (constants == _constants() and snapshot.matches(builder)
                        and buffer.template == prefix and tuple(buffer.literals) == literals):
                    keys, selected = dependencies
                    if all(dict.get(builder.cache, k) is entry for k, entry in zip(keys, selected)):
                        buffer.result.extend(suffix)
                        buffer.template.clear(); buffer.template.extend(tail)
                        buffer.literals.clear(); buffer.literals.extend(tail_literals)
                        _note_replay('REPLAY_USED')
                        return True
                    _note_replay("REPLAY_DEPENDENCY_CHANGED")
                self.disabled.add(key)
                self.plans.pop(key)
                _note_replay('REPLAY_CHAIN_REJECTED')
                return False
            _note_replay('PLAN_NEW')
            if len(self.plans) >= MAX_FRAME_PLANS:
                _note_replay('PLAN_COUNT_CAP')
                return False
            if len(self.disabled) >= MAX_FRAME_PLANS:
                self.exhausted = True
                self.plans.clear(); self.disabled.clear()
                _note_replay('DISABLED_COUNT_CAP')
                return False
            constants = _constants()
            if constants is None:
                self.disabled.add(key)
                _note_replay('CONSTANTS_INVALID')
                return False
            snapshot = _Snapshot(builder, value, self.budget)
            if not snapshot.valid:
                self.disabled.add(key)
                _note_replay('FRAME_SNAPSHOT_REJECTED')
                return False
            prefix, literals = bytes(buffer.template), tuple(buffer.literals)
            boundary = len(buffer.result)
            trace, old_trace = _Dependencies(), self.trace
            self.trace = trace
            try:
                # Busy suppresses this hook during the unchanged recursive
                # walk. The eligibility/named/small-plain checks preceding the
                # hook cannot emit a boundary for a collection reaching here.
                _note_replay('PLAN_CAPTURE_ENTER')
                builder._append(value, name, buffer, root=root)
                _note_replay("PLAN_CAPTURE_RETURN")
            finally:
                self.trace = old_trace
            dependencies = (trace.finish(builder.cache, snapshot)
                            if constants == _constants() and snapshot.matches(builder) else None)
            if dependencies is not None:
                suffix = tuple(buffer.result[boundary:])
                tail, tail_literals = bytes(buffer.template), tuple(buffer.literals)
                extra = (sum(sys.getsizeof(part) for part in dependencies)
                         + sum(sys.getsizeof(k) for k in dependencies[0])
                         + sum(sys.getsizeof(part) for part in (prefix, literals, suffix, tail, tail_literals)))
                if self.budget[2] + snapshot.bytes + extra <= MAX_SNAPSHOT_BYTES:
                    self.plans[key] = (snapshot, constants, dependencies, prefix, literals,
                                       suffix, tail, tail_literals)
                    self.budget[0] += len(snapshot.nodes)
                    self.budget[1] += snapshot.references
                    self.budget[2] += snapshot.bytes + extra
                    _note_replay('PLAN_STORED')
                    return True
                _note_replay("PLAN_BYTES_CAP")
            self.disabled.add(key)
            _note_replay('PLAN_POST_CAPTURE_REJECTED')
            return True
        finally:
            self.busy = False

    def close(self):
        self.closed = True
        self.plans.clear(); self.candidates.clear(); self.disabled.clear()


def prepare_publication_storage(values, *, mutable, durable_limit, expansion_limit):
    """Preserve constructor/order/role proofs; accept no external reuse cache."""
    mutable = frozenset(mutable)
    scope, cache, shape_memo = _SectionScope(values, mutable), {}, {}
    prepared = {}
    try:
        for role, value in values.items():
            # Calling the real __init__ on this exact instance preserves the
            # producer's constructor wrappers and their ENTER/EXIT events.
            instance = packed.PreparedPackedStorage.__new__(packed.PreparedPackedStorage)
            grant = packed._PublicationGrant(instance, value, cache, scope)
            token = packed._PUBLICATION_CONSTRUCTION.set(grant)
            try:
                packed.PreparedPackedStorage.__init__(instance, value, mutable=mutable,
                    durable_limit=durable_limit, expansion_limit=expansion_limit,
                    cache=cache, shape_memo=shape_memo)
                prepared[role] = instance
            finally:
                packed._PUBLICATION_CONSTRUCTION.reset(token)
                grant.close()
        # The publisher retains this cache to its original post-pack release
        # point. Advancing its lifetime can expose custom-object destructors.
        return prepared, cache
    finally:
        scope.close(); shape_memo.clear()
