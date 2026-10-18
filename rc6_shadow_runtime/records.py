"""Private row storage with shared field names and ordinary canonical JSON.

No market values are cached. A registry lives for one construction/read and
contains field names only. Public APIs continue to use ordinary dictionaries.
"""
from collections.abc import MutableMapping
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
import sys
from types import MappingProxyType

_DELETED = object()
_CURRENT = ContextVar('rc6_private_record_registry', default=None)


class _Schema:
    __slots__ = ('keys', 'index')

    def __init__(self, keys):
        self.keys = keys
        self.index = MappingProxyType({key: index for index, key in enumerate(keys)})


class RecordMap(MutableMapping):
    """Actual mutable cells; schema sharing does not share row values."""
    __slots__ = ('_schema', '_cells', '_extra', '_size')

    def __init__(self, schema, cells):
        self._schema, self._cells = schema, list(cells)
        self._extra, self._size = None, len(schema.keys)

    def __getitem__(self, key):
        index = self._schema.index.get(key)
        if index is not None:
            value = self._cells[index]
            if value is not _DELETED:
                return value
        elif self._extra is not None:
            return self._extra[key]
        raise KeyError(key)

    def get(self, key, default=None):
        index = self._schema.index.get(key)
        if index is not None:
            value = self._cells[index]
            return default if value is _DELETED else value
        return default if self._extra is None else self._extra.get(key, default)

    def __setitem__(self, key, value):
        index = self._schema.index.get(key)
        if index is not None:
            self._size += self._cells[index] is _DELETED
            self._cells[index] = value
        else:
            if self._extra is None:
                self._extra = {}
            self._size += key not in self._extra
            self._extra[key] = value

    def __delitem__(self, key):
        index = self._schema.index.get(key)
        if index is not None and self._cells[index] is not _DELETED:
            self._cells[index] = _DELETED
            self._size -= 1
        elif index is None and self._extra is not None and key in self._extra:
            del self._extra[key]
            self._size -= 1
        else:
            raise KeyError(key)

    def __len__(self):
        return self._size

    def __iter__(self):
        if self._extra is None and self._size == len(self._schema.keys):
            return iter(self._schema.keys)
        return (key for key, _ in self.items())

    def items(self):
        if self._extra is None and self._size == len(self._schema.keys):
            return zip(self._schema.keys, self._cells)
        def live():
            yield from ((key, value) for key, value in zip(self._schema.keys, self._cells)
                        if value is not _DELETED)
            if self._extra is not None:
                yield from self._extra.items()
        return live()

    def as_dict(self):
        return dict(self.items())

    def copy(self):
        return self.as_dict()

    def __deepcopy__(self, memo):
        result = RecordMap(self._schema, ())
        memo[id(self)] = result
        result._cells = deepcopy(self._cells, memo)
        # deepcopy must retain the deletion sentinel's identity.
        for index, value in enumerate(self._cells):
            if value is _DELETED:
                result._cells[index] = _DELETED
        result._extra = deepcopy(self._extra, memo)
        result._size = self._size
        return result


class RecordRegistry:
    """Bounded, data-free schemas. Unknown/oversized shapes stay dictionaries."""
    __slots__ = ('schemas', 'charged_bytes')
    MAX_SCHEMAS = 64
    MAX_FIELDS = 128
    MAX_BYTES = 128 * 1024

    def __init__(self):
        self.schemas, self.charged_bytes = {}, 0

    def pack(self, value):
        if type(value) is not dict or not 4 <= len(value) <= self.MAX_FIELDS:
            return value
        keys = tuple(value)
        if any(type(key) is not str or len(key) > 128 for key in keys):
            return value
        schema = self.schemas.get(keys)
        if schema is None:
            cost = sys.getsizeof(keys) + 512 + sum(sys.getsizeof(key)+64 for key in keys)
            if len(self.schemas) >= self.MAX_SCHEMAS or self.charged_bytes+cost > self.MAX_BYTES:
                return value
            schema = _Schema(keys)
            self.schemas[keys] = schema
            self.charged_bytes += cost
        return RecordMap(schema, value.values())


def json_default(value):
    # Preserve the pre-existing default=str contract for every other type.
    if type(value) is not RecordMap:
        return str(value)
    view = value.as_dict()
    # Pipeline/warmup/handler children are small records too. A single bounded
    # row can use the normal C encoder without materializing the full forest.
    for key, child in view.items():
        if type(child) is RecordMap:
            view[key] = child.as_dict()
    return view


def record(value):
    registry = _CURRENT.get()
    return value if registry is None else registry.pack(value)


@contextmanager
def record_context(enabled=True):
    if type(enabled) is not bool:
        raise ValueError('PRIVATE_RECORD_CONTEXT_MODE_INVALID')
    token = _CURRENT.set(RecordRegistry() if enabled else None)
    try:
        yield
    finally:
        _CURRENT.reset(token)


def checkpoint_decoder():
    registry = RecordRegistry()
    def decode(value):
        # Only the two causal funnel row roles are materialized differently.
        # Their values still participate in the complete envelope digest.
        if ('identity' in value and 'stages' in value and 'session' in value
                and 'strategy_id' in value):
            return registry.pack(value)
        return value
    return decode


def pack_telemetry(plans, registry):
    for plan in plans.values():
        for index, row in enumerate(plan.get('telemetry', [])):
            for field in ('pipeline', 'warmup_progress'):
                if type(row.get(field)) is dict:
                    row[field] = registry.pack(row[field])
            plan['telemetry'][index] = registry.pack(row)
