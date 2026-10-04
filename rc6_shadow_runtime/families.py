"""Bounded observations for each existing financial family, without a writer.

The runtime calls this bridge with its catalogue and dated source caches. It
never opens a broker, changes contracts, runs a lifecycle or grants entry
authority. Capture clocks prove availability, never quote freshness. Missing
provider clocks, units and terms remain visible as NO_VERIFICADO.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping
import json
import math
from pathlib import Path
import sqlite3
from time import monotonic

from am_us_equity_calendar_rc6 import calendar_status
from bs_instrument_contracts import FAMILIES, contract_from_metadata, family_name
from fe_fixed_income_nominal_contract_rc6 import NominalEvidence, evaluate as nominal_contract
from rc6_dynamic_universe.common import digest, identity, stamp
from rc6_dynamic_universe.routing import (
    dispatch_observation, option_observation_universe, strategy_route,
)
from rc6_dynamic_universe.sources import MARKETS, TERMS, SOURCE_ALIASES

MAX_ROWS = 20000
QUERY_BUDGET_SECONDS = 0.5
QUOTE_TTL_SECONDS = 120
IDENTITY_KEYS = ("ticker", "instrument_type", "market", "currency", "settlement")
_ALIASES = {
    "ratio": ("ratio", "conversion_ratio"),
    "quote_basis_nominal": ("quote_basis_nominal", "price_quote_unit", "provider_nominales_en_precio"),
    "quantity_step_nominal": ("quantity_step_nominal", "paper_quantity_step_nominal", "quantity_step", "order_quantity_step"),
    "minimum_nominal": ("minimum_nominal", "paper_minimum_nominal", "minimum_quantity", "quantity_min"),
    "yield": ("yield", "yield_to_maturity", "tir"),
    "duration": ("duration", "modified_duration", "modifiedDuration"),
    "parity": ("parity", "paridad"),
    "flows": ("flows", "cashflows", "cash_flows"),
    "nav": ("nav", "nav_value", "unit_value"),
    "iv": ("iv", "implied_volatility"),
    "strike": ("strike", "strike_price"),
    "strike_unit": ("strike_unit", "strike_price_unit"),
    "annual_rate_fraction": ("annual_rate_fraction",),
}


def _mapping(value):
    if isinstance(value, Mapping):
        return dict(value)
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _time(value):
    try:
        return stamp(value)
    except (ValueError, TypeError, OverflowError):
        return None


def _fresh(source_at, received_at, at, ttl=QUOTE_TTL_SECONDS):
    source, received = _time(source_at), _time(received_at)
    return bool(source and received and source <= received <= at
                and (ttl is None or 0 <= (at-source).total_seconds() <= ttl))


def _number(value, *, positive=False, signed=False):
    try:
        result = float(value)
        if isinstance(value, bool) or not math.isfinite(result) or (positive and result <= 0) or (not signed and result < 0):
            return None
        return result
    except (ValueError, TypeError, OverflowError):
        return None


def _key(row):
    """Normalize explicit source aliases, without guessing identity defaults."""
    ticker = row.get("ticker") or row.get("symbol")
    family = row.get("instrument_type") or row.get("asset_class") or row.get("asset_type") or row.get("family")
    settlement = row.get("settlement") or row.get("term") or row.get("settlement_code")
    try:
        normalized = dict(ticker=ticker, instrument_type=family_name(family),
                          market=MARKETS.get(str(row.get("market") or "").upper(), row.get("market")),
                          currency=row.get("currency"),
                          settlement=TERMS.get(str(settlement or "").upper(), settlement))
        key = identity(normalized)
        for name, value in (("symbol", key[0]), ("asset_class", key[1]), ("asset_type", key[1]),
                            ("family", key[1]), ("term", key[4]), ("settlement_code", key[4])):
            if row.get(name) in (None, ""):
                continue
            actual = str(row[name]).strip().upper()
            actual = family_name(actual) if name in {"asset_class", "asset_type", "family"} else TERMS.get(actual, actual)
            if actual != value:
                return None
        return key
    except (ValueError, TypeError):
        return None


def _views(row):
    """Known evidence containers only; no arbitrary provider payload exposure."""
    yield row
    for name in ("metadata", "financial_contract_v17", "paper_family_contract_v1",
                 "paper_caucion_contract_v1", "fixed_income_evidence", "fixed_income_analytics",
                 "option_chain_evidence", "cedear_features"):
        value = row.get(name)
        if isinstance(value, Mapping):
            nested = {**{key: row.get(key) for key in ("source", "source_at", "received_at", "observed_at")}, **value}
            if value.get("metadata_source"):
                nested["source"] = value["metadata_source"]
            yield from _views(nested)


def _field(name, rows, *, at, ttl=QUOTE_TTL_SECONDS, static=False, signed=False, numeric=False):
    values = []
    for row in rows:
        for view in _views(row):
            value = next((view[key] for key in _ALIASES.get(name, (name,)) if view.get(key) is not None), None)
            if value is None:
                continue
            envelope = dict(view)
            if isinstance(value, Mapping) and "value" in value:
                field = dict(value)
                envelope.update(field)
                value = field.get("value")
                # Independently dated features cannot borrow either parent
                # quote clock when their own publication was later/missing.
                envelope["source_at"] = field.get("source_at") or field.get("provider_observed_at") or field.get("observed_at")
                envelope["received_at"] = field.get("received_at") or field.get("published_at")
                envelope["observed_at"] = field.get("observed_at")
            evidence = view.get(name+"_evidence")
            if isinstance(evidence, Mapping):
                envelope.update(evidence)
                envelope["source_at"] = evidence.get("source_at") or evidence.get("provider_observed_at") or evidence.get("observed_at")
                envelope["received_at"] = evidence.get("received_at") or evidence.get("published_at")
                envelope["observed_at"] = evidence.get("observed_at")
            source_at = envelope.get("source_at") or envelope.get("provider_observed_at")
            if static:
                source_at = source_at or envelope.get("observed_at")
            received_at = envelope.get("received_at") or envelope.get("published_at") or envelope.get("observed_at")
            if static:
                received_at = received_at or source_at
            source = envelope.get("source") or envelope.get("metadata_source")
            if not source or not _fresh(source_at, received_at, at, ttl):
                continue
            if numeric:
                value = _number(value, signed=signed, positive=name in {"ratio", "ccl", "nav", "strike", "cash_multiplier"})
                if value is None:
                    continue
            elif name == "greeks":
                if not isinstance(value, Mapping):
                    continue
                value = {key: parsed for key in ("delta", "gamma", "theta", "vega", "rho")
                         if (parsed := _number(value.get(key), signed=True)) is not None}
                if not value:
                    continue
            elif name == "flows":
                if not isinstance(value, list) or not value or len(value) > 250:
                    continue
                cleaned = []
                for flow in value:
                    if not isinstance(flow, Mapping):
                        continue
                    payment_at = _time(flow.get("payment_at") or flow.get("at"))
                    amount = _number(flow.get("amount"), signed=True)
                    currency = flow.get("currency")
                    if payment_at and amount is not None and currency in {"ARS", "USD", "USD_MEP", "USD_CCL"}:
                        cleaned.append({"payment_at": payment_at.isoformat(), "amount": amount, "currency": currency})
                if len(cleaned) != len(value):
                    continue
                value = cleaned
            elif not isinstance(value, (str, bool, int, float)) or (isinstance(value, str) and len(value) > 256):
                continue
            if value is None:
                continue
            values.append((stamp(received_at), stamp(source_at), value, str(source)))
    if not values:
        return {"value": None, "status": "NO_VERIFICADO"}
    received, source_at, value, source = max(values, key=lambda item: (item[0], item[1]))
    return {"value": value, "status": "OBSERVED_FRESH" if ttl is not None else "VERIFIED_STATIC_INPUT",
            "source": source, "source_at": source_at.isoformat(), "received_at": received.isoformat(),
            "age_seconds": (at-source_at).total_seconds()}


def _read(database, at):
    path = Path(database).resolve(strict=True)
    connection = sqlite3.connect(path.as_uri()+"?mode=ro", uri=True, timeout=.005)
    connection.row_factory = sqlite3.Row
    result = {"metadata": [], "quotes": [], "cash": [], "truncated": [], "errors": []}
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        deadline = monotonic()+QUERY_BUDGET_SECONDS
        connection.set_progress_handler(lambda: int(monotonic() > deadline), 1000)
        state = connection.execute("SELECT mode,real_orders_sent FROM observer_state WHERE id=1").fetchone()
        if not state or state["mode"] != "PRODUCTION_PAPER" or state["real_orders_sent"] != 0:
            raise ValueError("PAPER_SAFETY_REQUIRED")
        tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table' LIMIT 200")}
        specs = (
            ("financial_instrument_catalog", "metadata", "last_seen_at", "last_seen_at", MAX_ROWS),
            ("market_snapshots", "quotes", "observed_at", "id", 5000),
            ("ppi_intraday_points", "quotes", "last_verified_at", "event_at", 5000),
            ("paper_equity_by_currency", "cash", "measured_at", "id", 500),
        )
        for table, kind, cutoff_column, order_column, limit in specs:
            if table not in tables:
                continue
            columns = {r[1] for r in connection.execute(f"PRAGMA table_info({table})")}
            if cutoff_column not in columns or order_column not in columns:
                result["errors"].append(table+":SOURCE_CLOCK_OR_SCHEMA_MISSING")
                continue
            try:
                rows = list(connection.execute(f"SELECT * FROM {table} WHERE julianday({cutoff_column})<=julianday(?) ORDER BY {order_column} DESC LIMIT ?", (at.isoformat(), limit+1)))
            except sqlite3.OperationalError:
                result["errors"].append(table+":READ_BUDGET_OR_SOURCE_UNAVAILABLE")
                break
            if len(rows) > limit:
                result["truncated"].append(table)
            for raw in rows[:limit]:
                row = dict(raw)
                if kind == "metadata":
                    row.update(metadata=_mapping(row.get("metadata_json")), source="PPI_CATALOG",
                               observed_at=row.get("last_seen_at"), received_at=row.get("last_seen_at"))
                    metadata = row["metadata"]
                    effective = metadata.get("_effective_observed_at") or row.get("last_seen_at")
                    row.update(observed_at=effective, received_at=effective)
                elif table == "market_snapshots":
                    row.update(financial_contract_v17=_mapping(row.get("contract_json")),
                               received_at=row.get("observed_at"), source_at=row.get("trade_at"),
                               source=row.get("source") or "PPI_MARKETDATA")
                elif table == "ppi_intraday_points":
                    row.update(received_at=row.get("last_verified_at"), source_at=row.get("event_at"),
                               last=row.get("price"), source=row.get("source") or "PPI_INTRADAY")
                    if not _fresh(row.get("event_at"), row.get("first_received_at"), at, None):
                        continue
                result[kind].append(row)
        return result
    finally:
        connection.close()


def _source_rows(sources):
    seen = 0
    for source, snapshot in (sources or {}).items():
        if source != "family_evidence" and source != "IOL_FAMILY_REFERENCE" and source not in SOURCE_ALIASES:
            continue
        if isinstance(snapshot, list):
            blocks = [({}, snapshot)]
        elif isinstance(snapshot, Mapping):
            blocks = [(snapshot, snapshot.get("symbols") or snapshot.get("records") or snapshot.get("observations") or [])]
            for block in snapshot.get("sources", []) if isinstance(snapshot.get("sources"), list) else []:
                if isinstance(block, Mapping) and block.get("source") == "BYMA":
                    blocks.append(({**snapshot, **block}, block.get("records", [])))
        else:
            continue
        for envelope, rows in blocks:
            for raw in rows if isinstance(rows, list) else []:
                if not isinstance(raw, Mapping):
                    continue
                if seen >= MAX_ROWS:
                    yield None
                    return
                seen += 1
                quote = raw.get("quote") if isinstance(raw.get("quote"), Mapping) else {}
                row = {**raw, **quote}
                row["source"] = raw.get("source") if source == "family_evidence" else SOURCE_ALIASES.get(source, source)
                row["source_at"] = (quote.get("source_at") or quote.get("provider_observed_at") or quote.get("timestamp")
                                    or raw.get("source_at") or raw.get("provider_observed_at") or raw.get("timestamp"))
                row["received_at"] = (raw.get("received_at") or raw.get("captured_at") or raw.get("observed_at")
                                      or envelope.get("captured_at") or envelope.get("observed_at") or envelope.get("refreshed_at"))
                yield row


def _quote(rows, at):
    candidates = [row for row in rows if _time(row.get("received_at")) and stamp(row["received_at"]) <= at
                  and any(row.get(key) is not None for key in ("last", "price", "bid", "ask"))]
    if not candidates:
        return {"status": "NO_VERIFICADO", "price": None, "observable": False, "tradeable": False}
    # Last receipt per source wins even when a newer response invalidates a book.
    latest = {}
    for row in candidates:
        source = str(row.get("source") or "NO_VERIFICADO")
        if source not in latest or stamp(row["received_at"]) > stamp(latest[source]["received_at"]):
            latest[source] = row
    row = max(latest.values(), key=lambda value: stamp(value["received_at"]))
    price = _number(row.get("last", row.get("price")), positive=True)
    source_available = str(row.get("state") or row.get("source_state") or "READY").upper() in {"READY", "LIVE_FRESH", "FRESH", "OBSERVE_ONLY"}
    trade_fresh = source_available and _fresh(row.get("source_at"), row.get("received_at"), at)
    bid, ask = _number(row.get("bid"), positive=True), _number(row.get("ask"), positive=True)
    bid_size, ask_size = _number(row.get("bid_size"), positive=True), _number(row.get("ask_size"), positive=True)
    book_at = row.get("book_at") or row.get("source_at")
    book_fresh = source_available and _fresh(book_at, row.get("received_at"), at)
    spread = (ask-bid)/((ask+bid)/2)*10000 if bid and ask and ask >= bid else None
    contract = _mapping(row.get("financial_contract_v17"))
    unit = row.get("price_unit")
    if not unit and contract.get("family") in {"ACCIONES", "CEDEARS", "ETFS"} and _number(contract.get("cash_multiplier")) == 1 and contract.get("metadata_source"):
        unit = "PER_SHARE"
    result = {"status": "OBSERVED_FRESH" if trade_fresh and price else "NO_VERIFICADO",
              "source": row.get("source"), "source_at": row.get("source_at"), "received_at": row["received_at"],
              "price": price if trade_fresh else None, "price_unit": unit or "NO_VERIFICADO",
              "book_at": book_at, "book_status": "OBSERVED_FRESH" if book_fresh and spread is not None and bid_size and ask_size else "NO_VERIFICADO",
              "observable": bool(trade_fresh and price),
              "tradeable": bool(trade_fresh and price and book_fresh and spread is not None and spread <= 100 and bid_size and ask_size)}
    for name, value in (("bid", bid), ("ask", ask), ("bid_size", bid_size), ("ask_size", ask_size), ("spread_bps", spread)):
        result[name] = value if book_fresh else None
    return result


def _conflicted(rows, at):
    return any((row.get("_contract_conflicts") or _mapping(row.get("metadata")).get("_contract_conflicts")
                or row.get("change_pending") or row.get("revoked"))
               and _time(row.get("received_at")) and stamp(row["received_at"]) <= at for row in rows)


def _family_references(sources, at):
    """Show broad reference inventories without assigning an invented identity."""
    result = {}
    snapshot = (sources or {}).get("IOL_FAMILY_REFERENCE")
    if not isinstance(snapshot, Mapping):
        return result
    sections = snapshot.get("section_observed_at") or {}
    states = snapshot.get("section_states") or {}
    fci = snapshot.get("fci")
    if isinstance(fci, list):
        seen = sections.get("fci")
        available = bool(_time(seen) and stamp(seen) <= at)
        result["FCI"] = {"source": "IOL_FAMILY_REFERENCE", "inventory_count": min(len(fci), MAX_ROWS) if available else None,
                         "capture_observed_at": seen if _time(seen) and stamp(seen) <= at else None,
                         "source_state": states.get("fci", "NO_VERIFICADO") if available else "NO_VERIFICADO_AT_CUTOFF",
                         "identity_binding": "EXACT_IDENTITY_REQUIRED", "nav_freshness": "NO_VERIFICADO"}
    rates = snapshot.get("cauciones")
    if isinstance(rates, Mapping):
        result["CAUCIONES"] = {"source": "IOL_FAMILY_REFERENCE", "currency_references": []}
        for currency in ("ARS", "USD", "USD_MEP", "USD_CCL"):
            rows = rates.get(currency)
            if not isinstance(rows, list):
                continue
            seen = sections.get("caucion:"+currency)
            available = bool(_time(seen) and stamp(seen) <= at)
            result["CAUCIONES"]["currency_references"].append({
                "currency": currency, "rate_term_reference_count": min(len(rows), MAX_ROWS) if available else None,
                "capture_observed_at": seen if _time(seen) and stamp(seen) <= at else None,
                "source_state": states.get("caucion:"+currency, "NO_VERIFICADO") if available else "NO_VERIFICADO_AT_CUTOFF",
                "identity_binding": "EXACT_IDENTITY_REQUIRED", "rate_freshness": "NO_VERIFICADO"})
    return result


def family_reports(database, *, as_of, catalog, sources=None):
    """Dispatch every catalog row through its owner using existing offline data.

    ``sources`` accepts the regular BYMA/IOL/PPI source-cache identifiers,
    IOL_FAMILY_REFERENCE and optional identity-bound family_evidence rows.
    Fields can be independently clocked envelopes {value,source,source_at,
    received_at}; later publications never enter an earlier as_of report.
    """
    at = stamp(as_of)
    catalog = [dict(row) for row in catalog]
    if len(catalog) > 50000:
        raise ValueError("CATALOG_READ_BUDGET_EXCEEDED")
    data = _read(database, at)
    evidence = defaultdict(list)
    for row in [*data["metadata"], *data["quotes"], *catalog]:
        key = _key(row)
        if key and key[1] != "FUTUROS":
            evidence[key].append(row)
    source_count = 0
    for row in _source_rows(sources):
        if row is None:
            data["truncated"].append("SOURCE_SNAPSHOTS")
            break
        source_count += 1
        key = _key(row)
        if key and key[1] != "FUTUROS":
            evidence[key].append(row)
    quotes = {key: _quote(rows, at) for key, rows in evidence.items()}
    selected_options, excluded_options = [], []

    def equity(record, *, as_of):
        key = identity(record)
        result = {"status": "OBSERVE_ONLY", "quote": quotes.get(key, _quote([], at)),
                  "entry_authority": False, "momentum_signal": "NOT_EVALUATED_BY_FAMILY_OBSERVER"}
        if key[1] == "CEDEARS":
            result["cedear_features"] = {
                "ratio": _field("ratio", evidence[key], at=at, ttl=86400, static=True, numeric=True),
                "underlying_us_session": calendar_status(at),
                "ccl": _field("ccl", evidence[key], at=at, numeric=True),
                "local_divergence": _field("local_divergence", evidence[key], at=at, signed=True, numeric=True),
            }
        return result

    def fixed_income(record, *, as_of):
        key = identity(record)
        rows = evidence[key]
        terms = {name: _field(name, rows, at=at, ttl=None, static=True, numeric=True)
                 for name in ("quote_basis_nominal", "quantity_step_nominal", "minimum_nominal", "cash_multiplier")}
        basis = terms["quote_basis_nominal"]
        nominal = nominal_contract(NominalEvidence(key[0], key[1], key[2], key[3], key[4],
            basis["value"], terms["quantity_step_nominal"]["value"], terms["minimum_nominal"]["value"],
            basis.get("source", ""), basis.get("received_at", "")))
        valid = nominal["status"] == "VERIFIED_NOMINAL_CONTRACT"
        multiplier = terms["cash_multiplier"]["value"]
        if valid and (multiplier is None or abs(multiplier*basis["value"]-1) > 1e-9):
            valid = False
        conflict = _conflicted(rows, at)
        valid = valid and not conflict
        analytics = {name: _field(name, rows, at=at, ttl=300, numeric=name != "flows", signed=name in {"yield", "carry"})
                     for name in ("yield", "duration", "parity", "carry", "flows")}
        return {"status": "OBSERVE_ONLY", "quote": quotes.get(key, _quote([], at)), "terms": terms,
                "nominal_contract_status": "VERIFIED_NOMINAL_CONTRACT" if valid else "NO_VERIFICADO",
                "analytics": analytics, "cash_per_nominal_currency": key[3], "cash_per_nominal": (
                    quotes[key]["price"]*multiplier if valid and quotes.get(key, {}).get("price") is not None else None),
                "reason_codes": ["CONTRACT_CONFLICT_NO_VERIFICADO"] if conflict else [] if valid else ["QUOTE_BASIS_MULTIPLIER_OR_VN_NO_VERIFICADO"],
                "flow_application": "OBSERVATION_ONLY", "entry_authority": False}

    def options(record, *, as_of):
        key = identity(record)
        rows, quote = evidence[key], quotes.get(key, _quote([], at))
        terms = {name: _field(name, rows, at=at, ttl=None, static=True, numeric=name == "strike")
                 for name in ("underlying", "strike", "expires_at", "strike_unit", "option_right")}
        underlying = terms["underlying"]["value"]
        matches = [k for k in quotes if k[0] == str(underlying or "").upper()
                   and k[1] in {"ACCIONES", "CEDEARS", "ETFS"} and k[2:4] == key[2:4]]
        same_settlement = [k for k in matches if k[4] == key[4]]
        matches = same_settlement or matches
        underlying_rows = {}
        if len(matches) == 1:
            match = matches[0]
            underlying_rows[match[0]] = {**dict(zip(IDENTITY_KEYS, match)), **quotes[match]}
            # Existing standard BYMA contracts prove a strike per underlying
            # share. This is a dimensional contract derivation, not a ticker
            # parse or a guessed lot. Other contracts still need explicit units.
            if terms["strike_unit"]["value"] is None and match[1] in {"ACCIONES", "CEDEARS"}:
                for row in rows:
                    for view in _views(row):
                        source = str(view.get("metadata_source") or "")
                        if view.get("family") != "OPCIONES" or "BYMA_OPTION" not in source:
                            continue
                        if not _fresh(view.get("observed_at"), view.get("received_at"), at, None):
                            continue
                        try:
                            contract = contract_from_metadata(key[0], "OPCIONES", view)
                        except (ValueError, TypeError):
                            continue
                        if contract.underlying == match[0] and contract.currency == key[3]:
                            terms["strike_unit"] = {"value": "PER_SHARE", "status": "VERIFIED_STATIC_INPUT",
                                                     "source": source, "derivation": "STANDARD_BYMA_OPTION_STRIKE_PER_UNDERLYING_SHARE"}
                            break
        series = {**record, **quote, **{name: value["value"] for name, value in terms.items()}}
        series["source_at"] = quote.get("book_at")
        ancillary = {}
        for name in ("iv", "greeks", "open_interest", "volume"):
            metric = _field(name, rows, at=at, numeric=name in {"iv", "open_interest", "volume"})
            ancillary[name] = metric
            series[name] = metric["value"]
            series[name+"_evidence"] = metric
        universe = option_observation_universe([series], underlying_rows, as_of=at)
        if _conflicted(rows, at):
            universe["selected"] = []
            universe["excluded"] = [{"identity": key, "reason_codes": ["CONTRACT_CONFLICT_NO_VERIFICADO"]}]
        selected_options.extend(universe["selected"])
        excluded_options.extend(universe["excluded"])
        return {"status": "OBSERVE_ONLY", "terms": terms, "quote": quote, "ancillary": ancillary,
                "structurally_selected": bool(universe["selected"]),
                "reason_codes": [] if universe["selected"] else universe["excluded"][0]["reason_codes"],
                "selector": "UNDERLYING_EXPIRY_MONEYNESS_BOOK", "blind_round_robin": False,
                "entry_authority": False}

    def caucion(record, *, as_of):
        key = identity(record)
        rows = evidence[key]
        terms = {name: _field(name, rows, at=at, numeric=name in {"annual_rate_fraction", "term_days", "available_principal"})
                 for name in ("annual_rate_fraction", "term_days", "available_principal", "maturity_at")}
        cash_rows = [row for row in data["cash"] if row.get("currency") == key[3]]
        cash_row = max(cash_rows, key=lambda row: stamp(row["measured_at"])) if cash_rows else {}
        cash = {"value": _number(cash_row.get("cash")), "measured_at": cash_row.get("measured_at"),
                "status": "OBSERVED_PAPER_CASH" if _number(cash_row.get("cash")) is not None else "NO_VERIFICADO"}
        events = {"cash": cash, "terms": terms}
        event_values = {"cash": cash["value"], "cash_status": cash["status"],
                        "terms": {name: {"value": value["value"], "status": value["status"]} for name, value in terms.items()}}
        return {"status": "OBSERVE_ONLY", **events, "trigger": "CASH_RATE_TERM_OR_MATURITY_EVENT",
                "event_signature": digest(event_values), "intraday_equity_scanner": False,
                "reason_codes": ["TREASURY_TERMS_NO_VERIFICADO"] if any(value["value"] is None for value in terms.values()) else [],
                "entry_authority": False}

    def fci(record, *, as_of):
        key = identity(record)
        rows = evidence[key]
        terms = {name: _field(name, rows, at=at, ttl=86400, numeric=name == "nav")
                 for name in ("nav", "nav_date", "subscription_status", "redemption_term", "cutoff_time")}
        return {"status": "OBSERVE_ONLY", "terms": terms, "trigger": "NAV_SUBSCRIPTION_REDEMPTION_EVENT",
                "intraday_equity_scanner": False, "event_signature": digest({name: {"value": value["value"], "status": value["status"]} for name, value in terms.items()}),
                "reason_codes": ["FUND_NAV_OR_LIFECYCLE_NO_VERIFICADO"] if any(value["value"] is None for value in terms.values()) else [],
                "entry_authority": False}

    handlers = {"EQUITY_SPOT": equity, "FIXED_INCOME_ANALYTICS": fixed_income,
                "OPTION_STRUCTURE": options, "TREASURY": caucion, "FUND_NAV": fci}
    instruments = []
    for record in catalog:
        key = _key(record)
        if key is None:
            instruments.append({"identity": [record.get(name) for name in IDENTITY_KEYS],
                                "status": "OBSERVE_ONLY", "observation_status": "EXCLUDED_IDENTITY",
                                "reason_codes": ["EXACT_IDENTITY_REQUIRED"], "entry_authority": False})
            continue
        normalized = {**record, **dict(zip(IDENTITY_KEYS, key))}
        report = dispatch_observation(normalized, handlers, as_of=at)
        if key[1] == "FUTUROS":
            report["handler_result"] = {"status": "DELEGATED", "owner_issue": 453, "owner_workstream": "WS_MOTOR_16",
                                        "lifecycle_called": False, "entry_authority": False}
        instruments.append({"identity": list(key), **report, "status": "OBSERVE_ONLY"})
    counts = Counter(str(row["identity"][1] or "UNKNOWN") for row in instruments)
    families = []
    references = _family_references(sources, at)
    for name in sorted(FAMILIES | set(counts)):
        route = strategy_route(name)
        families.append({**route, "status": "OBSERVE_ONLY", "catalog_count": counts[name],
                         "dispatch_count": sum(row.get("handler_result") is not None for row in instruments if str(row["identity"][1] or "UNKNOWN") == name),
                         "reference_evidence": references.get(name, {}),
                         "entry_authority": False})
    selected_options.sort(key=lambda row: (row["underlying"], stamp(row["expires_at"]), row["moneyness"], row["identity"]))
    return {"schema": "RC6_SHADOW_FAMILY_RUNTIME_V1", "as_of": at.isoformat(), "status": "OBSERVE_ONLY",
            "catalog_count": len(catalog), "catalog_preserved": len(instruments) == len(catalog),
            "instruments": instruments, "families": families,
            "option_observation_universe": {"status": "OBSERVE_ONLY", "selected": selected_options, "excluded": excluded_options,
                                            "selector": "UNDERLYING_EXPIRY_MONEYNESS_BOOK", "blind_round_robin": False},
            "evidence_read": {"database_effect": "READ_ONLY", "query_only": True, "source_rows": source_count,
                              "row_limit": MAX_ROWS, "query_budget_seconds": QUERY_BUDGET_SECONDS,
                              "truncated": data["truncated"], "errors": data["errors"]},
            "safety": {"mode": "SHADOW", "observer_mode": "PRODUCTION_PAPER", "real_orders_sent": 0,
                       "entry_authority": False, "real_order_routes": "NOT_CALLED", "lifecycle_mutations": 0}}
