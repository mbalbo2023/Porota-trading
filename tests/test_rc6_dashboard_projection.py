"""Native four-member committed projections and bounded caller contracts."""
from copy import deepcopy
from contextlib import contextmanager
import json
import sqlite3
from threading import Event, get_ident, enumerate as threads
from time import monotonic

import pytest

from rc6_trader_dashboard.datasets import shadow_rows
from rc6_trader_dashboard.projected_generation import read_projected, VERIFICATION_LEVEL
from rc6_trader_dashboard.projection import Projection, Store
from rc6_trader_dashboard.routes import build_page
from tests.rc6_dashboard_native_fixture import native_fixture
from tests.test_rc6_dashboard_convergence import inventory


@pytest.fixture(scope="module")
def projected_native(tmp_path_factory):
    # The positive shape comes exclusively from the canonical writer/reader.
    import rc6_shadow_runtime.persistence as persistence
    fixture = native_fixture(tmp_path_factory.mktemp("native-four-member-cut"))
    cut = persistence.read_committed_projection(fixture.root, deadline=monotonic()+1)
    return fixture, cut


def test_native_four_role_projection_preserves_cut_and_never_decodes_large_originals(projected_native, monkeypatch):
    import rc6_shadow_runtime.persistence as persistence
    fixture, cut = projected_native
    before = inventory(fixture.database)
    forbidden = {fixture.cut["manifest"]["files"][role]["payload_digest"] for role in ("report", "checkpoint")}
    original = persistence.decode_storage
    def native_bounded_decode(payload, *args, **kwargs):
        if isinstance(payload, dict):
            assert payload.get("logical_sha256") not in forbidden, "UI decoded an original report/checkpoint"
        return original(payload, *args, **kwargs)
    monkeypatch.setattr(persistence, "decode_storage", native_bounded_decode)
    deadline = monotonic()+1
    projected = read_projected(fixture.root, persistence.read_committed_projection, deadline=deadline)
    assert projected["state"] == "COMMITTED_COHERENT_SHADOW"
    assert projected["verification_level"] == VERIFICATION_LEVEL
    assert projected["pointer"] == fixture.cut["pointer"] == cut["pointer"]
    assert set(projected["export_contract"]["verified_payloads"]) == {"report", "checkpoint", "status", "projection"}
    assert projected["query_bytes"] < 4*1024**2
    assert monotonic() < deadline
    with Store(fixture.database, now=fixture.as_of) as store:
        projection = Projection(store)
        for kind in ("opportunities", "capacity", "families", "signals", "experiments", "exits"):
            page = shadow_rows(projection, kind)
            assert page.state == "AVAILABLE" and page.rows and page.total
            assert len(page.rows) <= 10
            assert VERIFICATION_LEVEL in page.source
    assert inventory(fixture.database) == before


def test_native_projection_filters_and_pages_reach_last_catalog_identity(projected_native):
    fixture, cut = projected_native
    last = "T024"
    with Store(fixture.database, now=fixture.as_of) as store:
        unfiltered = shadow_rows(Projection(store), "opportunities")
        matched = shadow_rows(Projection(store, {"q": last, "currency": "ARS"}), "opportunities")
        assert matched.total == 2 and all(row["symbol"] == last for row in matched.rows)
        next_page = shadow_rows(Projection(store, {"offset": "10"}), "opportunities")
        assert next_page.total == unfiltered.total and len(next_page.rows) == 10
        assert not {(row["engine"], row["identity"]) for row in next_page.rows} & {(row["engine"], row["identity"]) for row in unfiltered.rows}
        injected = shadow_rows(Projection(store, {"q": "' OR 1=1 --"}), "opportunities")
        assert injected.state == "AVAILABLE" and injected.total == 0 and not injected.rows


def test_native_projection_unpublished_filter_is_unknown_and_preserves_source_population(projected_native):
    fixture, native = projected_native
    with Store(fixture.database, now=fixture.as_of) as store:
        projection = Projection(store, {"channel": "NATIVE_FACTUAL"})
        page = shadow_rows(projection, "opportunities")
        assert page.state == "NO_VERIFICADO" and not page.rows and page.total is None
        assert "FILTER_NOT_PUBLISHED_FOR_DATASET:channel" in page.reason
        assert str(native["dataset_pages"]["opportunities"]["total"]) in page.reason
        assert projection.funnel_scope["state"] == "AVAILABLE"
        assert projection.funnel_scope["selected"]["channel"] == "NATIVE_FACTUAL"


def test_native_projection_full_identity_filters_keep_table_and_funnel_in_one_scope(projected_native):
    fixture, native = projected_native
    raw = native["dataset_pages"]["opportunities"]["rows"][0]
    symbol, family, market, currency, settlement = raw["identity"]
    filters = dict(q=symbol, family=family, market=market, currency=currency, settlement=settlement,
                   identity=json.dumps(raw["identity"]))
    with Store(fixture.database, now=fixture.as_of) as store:
        projection = Projection(store, filters)
        page = shadow_rows(projection, "opportunities")
        assert page.state == "AVAILABLE" and page.total == 2
        assert all(json.loads(row["identity"]) == raw["identity"] for row in page.rows)
        selected = projection.funnel_scope["selected"]
        assert selected["identity"] == [symbol, family, settlement, currency, market]
        assert projection.funnel_scope["counts"]["READY"] == selected["stages"].get("CATALOG_READY")
        if "CATALOG_READY" not in selected["stages"]:
            assert projection.funnel_scope["counts"]["READY"] is None
        assert projection.funnel_scope["counts"]["PAPER"] == selected["stages"].get("PAPER_OPENED")


@pytest.mark.parametrize("dimension", ("family", "market", "currency", "settlement", "q", "state", "identity"))
def test_projected_payload_that_contradicts_its_filter_is_rejected_without_refiltering(projected_native, dimension):
    from rc6_shadow_runtime.persistence import read_committed_projection
    fixture, native = projected_native
    row = native["dataset_pages"]["opportunities"]["rows"][0]
    identity = list(row["identity"])
    selectors = dict(zip(("q", "family", "market", "currency", "settlement"), identity))
    selectors.update(state=row["state"], identity=json.dumps(identity))
    filters = {dimension: selectors[dimension]}
    cut = deepcopy(read_committed_projection(fixture.root, filters=filters, deadline=monotonic()+1))
    row = cut["dataset_pages"]["opportunities"]["rows"][0]
    if dimension == "state":
        row["state"] = "STATE_NOT_SELECTED"
    else:
        index = {"family": 1, "market": 2, "currency": 3, "settlement": 4, "q": 0, "identity": 3}[dimension]
        row["identity"][index] = "DIMENSION_NOT_SELECTED"
    with Store(fixture.database, now=fixture.as_of) as store:
        projection = Projection(store, filters)
        projection.shadow = read_projected(fixture.root, lambda *_args, **_kwargs: cut, filters=filters)
        page = shadow_rows(projection, "opportunities")
        assert page.state == "CONTRACT_ERROR" and not page.rows and page.total is None
        assert page.reason == "PROJECTED_NATIVE_ROW_CONTRACT_REJECTED"


@pytest.mark.parametrize("dimension", ("currency", "channel"))
@pytest.mark.parametrize("target", ("selected", "group"))
def test_projected_funnel_payload_outside_requested_scope_is_rejected(projected_native, dimension, target):
    fixture, native = projected_native
    cut = deepcopy(native)
    selected = cut["funnel_scope"]["selected"]
    filters = {dimension: selected[dimension]}
    (selected if target == "selected" else cut["funnel_scope"]["groups"][0])[dimension] = "DIMENSION_NOT_SELECTED"
    rejected = read_projected(fixture.root, lambda *_args, **_kwargs: cut, filters=filters)
    assert rejected["state"] == "NO_VERIFICADO" and rejected["report"] == {}


@pytest.mark.parametrize("deadline", (float("nan"), float("inf"), -float("inf"), True))
def test_nonfinite_or_boolean_deadline_cannot_remove_the_read_budget(projected_native, deadline):
    fixture, native = projected_native
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Invalid deadline reached the canonical reader")
    rejected = read_projected(fixture.root, forbidden, deadline=deadline)
    assert rejected["state"] == "NO_VERIFICADO" and rejected["report"] == {}


@pytest.mark.parametrize("mutation", (
    "export_schema", "verification_level", "custody", "typed_safety", "role_set", "role_digest", "derivation",
    "header_cut", "page_total", "page_offset", "page_oversized", "funnel_total", "funnel_count",
    "funnel_offset", "output_budget", "envelope_shape",
))
def test_projection_adapter_rejects_mutations_of_a_real_native_query(projected_native, mutation):
    fixture, native = projected_native
    cut = deepcopy(native)
    contract, page, scoped = cut["export_contract"], cut["dataset_pages"]["opportunities"], cut["funnel_scope"]
    if mutation == "export_schema": contract["schema"] = "UNKNOWN"
    elif mutation == "verification_level": contract["verification_level"] = "FULL_LOGICAL_SEMANTICS"
    elif mutation == "custody": contract["custody"] = "EXTERNAL_AUTHENTICATION_UNPROVEN"
    elif mutation == "typed_safety": contract["role_headers"]["checkpoint"]["safety"]["real_orders_sent"] = False
    elif mutation == "role_set": contract["verified_payloads"].pop("checkpoint")
    elif mutation == "role_digest": contract["verified_payloads"]["report"]["payload_digest"] = "0"*64
    elif mutation == "derivation": contract["derivation"]["report"] = "0"*64
    elif mutation == "header_cut": contract["role_headers"]["projection"]["sequence"] = True
    elif mutation == "page_total": page["total"] = 1
    elif mutation == "page_offset": page["offset"] = 1
    elif mutation == "page_oversized": page["rows"] = [page["rows"][0]]*11
    elif mutation == "funnel_total": scoped["total_groups"] = 0
    elif mutation == "funnel_count": scoped["counts"]["PAPER"] = 999
    elif mutation == "funnel_offset": scoped["groups_offset"] = 1
    elif mutation == "output_budget": cut["report"]["invalid_padding"] = "x"*(4*1024**2)
    elif mutation == "envelope_shape": cut["pointer"] = None
    rejected = read_projected(fixture.root, lambda *_args, **_kwargs: cut)
    assert rejected["state"] == "NO_VERIFICADO" and rejected["report"] == {}
    assert rejected["reason"] == "COMMITTED_PROJECTION_REJECTED"


@pytest.mark.parametrize("failure", (ImportError, sqlite3.OperationalError, sqlite3.DatabaseError))
def test_projection_reader_failure_is_sanitized_and_has_no_full_reader_fallback(projected_native, failure):
    fixture, _ = projected_native
    def fails(*_args, **_kwargs):
        raise failure("SYNTHETIC_PRIVATE_MARKER_DO_NOT_DISPLAY")
    cut = read_projected(fixture.root, fails)
    assert cut["state"] == "NO_VERIFICADO" and cut["report"] == {}
    assert "SYNTHETIC_PRIVATE_MARKER" not in json.dumps(cut)


def test_request_captures_source_and_same_filtered_cut_concurrently_with_one_deadline(projected_native, monkeypatch):
    import rc6_audit_evidence.sqlite_snapshot as snapshot
    import rc6_shadow_runtime.persistence as persistence
    fixture, native = projected_native
    before = inventory(fixture.database)
    copy_started, reader_started = Event(), Event()
    original_copy, original_reader = snapshot.readonly_copy, persistence.read_committed_projection
    captures = []

    @contextmanager
    def counted_copy(*args, **kwargs):
        copy_started.set()
        assert reader_started.wait(.5), "The canonical reader did not start during source capture"
        captures.append(("source", get_ident(), kwargs["deadline"]))
        with original_copy(*args, **kwargs) as connection:
            yield connection

    def counted_reader(*args, **kwargs):
        reader_started.set()
        assert copy_started.wait(.5), "Source capture did not overlap the reader"
        captures.append(("projection", get_ident(), kwargs["deadline"], kwargs["filters"], kwargs["offset"]))
        cut = original_reader(*args, **kwargs)
        assert cut["pointer"] == native["pointer"]
        return cut

    monkeypatch.setattr(snapshot, "readonly_copy", counted_copy)
    monkeypatch.setattr(persistence, "read_committed_projection", counted_reader)
    html, _ = build_page("/en-vivo/oportunidades", {"q": "T024", "currency": "ARS", "offset": "invalid", "funnel_offset": "-2"},
                         fixture.database, now=fixture.as_of)
    assert VERIFICATION_LEVEL in html and "T024" in html
    source, projected = sorted(captures, key=lambda row: row[0], reverse=True)
    assert source[0] == "source" and projected[0] == "projection"
    assert source[1] != projected[1] and source[2] == projected[2]
    assert projected[3] == {"q": "T024", "currency": "ARS", "funnel_offset": "0"} and projected[4] == 0
    assert not any(thread.name.startswith("rc6-dashboard-shadow") for thread in threads())
    assert inventory(fixture.database) == before


@pytest.mark.parametrize("different", ({"currency": "USD"}, {"offset": "10"}, {"funnel_offset": "10"}))
def test_request_prefetch_never_reuses_a_different_projection_selection(projected_native, monkeypatch, different):
    import rc6_shadow_runtime.persistence as persistence
    fixture, native = projected_native
    original_reader = persistence.read_committed_projection
    calls = []

    def counted_reader(*args, **kwargs):
        calls.append((kwargs["filters"], kwargs["offset"], kwargs["deadline"]))
        return original_reader(*args, **kwargs)

    monkeypatch.setattr(persistence, "read_committed_projection", counted_reader)
    with Store(fixture.database, now=fixture.as_of, shadow_filters={"currency": "ARS"}) as store:
        first = Projection(store, {"currency": "ARS"}).shadow
        assert first["pointer"] == native["pointer"]
        assert Projection(store, {"currency": "ARS"}).shadow is first
        other = Projection(store, {**{"currency": "ARS"}, **different}).shadow
        assert other["state"] == "COMMITTED_COHERENT_SHADOW" and other["pointer"] == native["pointer"]
        assert len(calls) == 2 and calls[0][:2] != calls[1][:2]
        assert all(call[2] == store.deadline for call in calls)
    assert not any(thread.name.startswith("rc6-dashboard-shadow") for thread in threads())


def test_request_prefetch_does_not_override_an_explicit_generation_reader(projected_native):
    fixture, _ = projected_native
    calls = []
    def explicit_reader(*_args, **_kwargs):
        calls.append(1)
        return fixture.cut
    with Store(fixture.database, now=fixture.as_of, shadow_filters={}) as store:
        selected = Projection(store, generation_reader=explicit_reader).shadow
        assert selected["state"] == "COMMITTED_COHERENT_SHADOW"
        assert calls == [1] and "dataset_pages" not in selected
    assert store._shadow_future.done()
    assert not any(thread.name.startswith("rc6-dashboard-shadow") for thread in threads())


@pytest.mark.parametrize("failure", ("source_capture", "view"))
def test_request_joins_the_native_reader_on_capture_or_render_failure(projected_native, monkeypatch, failure):
    import rc6_audit_evidence.sqlite_snapshot as snapshot
    import rc6_shadow_runtime.persistence as persistence
    fixture, native = projected_native
    before = inventory(fixture.database)
    started, released, finished = Event(), Event(), Event()
    original_reader = persistence.read_committed_projection

    def held_reader(*args, **kwargs):
        started.set()
        assert released.wait(.5)
        try:
            cut = original_reader(*args, **kwargs)
            assert cut["pointer"] == native["pointer"]
            return cut
        finally:
            finished.set()

    monkeypatch.setattr(persistence, "read_committed_projection", held_reader)
    if failure == "source_capture":
        @contextmanager
        def rejected_copy(*_args, **_kwargs):
            assert started.wait(.5)
            raise snapshot.SnapshotError("SOURCE_SNAPSHOT_BUSY")
            yield  # Context manager rejects before opening any connection.
        monkeypatch.setattr(snapshot, "readonly_copy", rejected_copy)
        with Store(fixture.database, now=fixture.as_of, shadow_filters={}) as store:
            assert store.connection is None and "DATABASE_READ_UNAVAILABLE" in store.errors
            released.set()
    else:
        with pytest.raises(LookupError, match="RENDER_ABORTED"):
            with Store(fixture.database, now=fixture.as_of, shadow_filters={}):
                assert started.wait(.5)
                released.set()
                raise LookupError("RENDER_ABORTED")
    assert finished.is_set()
    assert not any(thread.name.startswith("rc6-dashboard-shadow") for thread in threads())
    assert inventory(fixture.database) == before


def test_request_waits_for_unused_reader_then_discards_body_at_its_original_deadline(projected_native, monkeypatch):
    from bs4 import BeautifulSoup
    import rc6_shadow_runtime.persistence as persistence
    import rc6_trader_dashboard.shell as shell
    fixture, native = projected_native
    before = inventory(fixture.database)
    original_reader = persistence.read_committed_projection
    finished, calls = Event(), []

    def held_reader(*args, **kwargs):
        calls.append(kwargs["deadline"])
        cut = original_reader(*args, **kwargs)
        assert cut["pointer"] == native["pointer"]
        # Deliberate negative scheduling: retain a real result past this same
        # request's deadline; the view below never consumes its shadow property.
        Event().wait(max(0, kwargs["deadline"] - monotonic()) + .02)
        finished.set()
        return cut

    monkeypatch.setattr(persistence, "read_committed_projection", held_reader)
    monkeypatch.setattr(shell, "policy_state", lambda _projection: "NO_VERIFICADO")
    html, _ = build_page("/instrumentos", {}, fixture.database, now=fixture.as_of)
    assert len(calls) == 1 and finished.is_set() and monotonic() >= calls[0]
    assert "Corte de lectura no disponible dentro del presupuesto" in html
    assert not BeautifulSoup(html, "html.parser").select("tr[data-row]")
    assert not any(thread.name.startswith("rc6-dashboard-shadow") for thread in threads())
    assert inventory(fixture.database) == before
