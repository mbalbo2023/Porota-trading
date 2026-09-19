"""Vista unificada del universo operativo HF6.

Solo lectura. Reutiliza la base PAPER del dashboard y no contiene rutas de escritura.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta

from ak_byma_calendar import es_dia_habil_operativo

from fastapi import Header, Query, Request
from fastapi.responses import HTMLResponse

import bg_paper_dashboard as bg


_installed = False


def _int(value):
    try:
        return int(value or 0)
    except Exception:
        return 0


def _family_state(observed, ready, market_seen, positions):
    if ready > 0:
        return "READY_PAPER"
    if observed > 0 or market_seen > 0:
        return "OBSERVED_BLOCKED"
    if positions > 0:
        return "LEGACY_POSITION"
    return "NO_INSTRUMENTS"


def _capability_text(items):
    if not items:
        return "sin capacidad observada"

    return ", ".join(
        f"{name}={count}"
        for name, count in sorted(items.items())
    )


def _page():
    coverage = bg._rows("""
        SELECT
            instrument_type,
            declared,
            queries,
            observed_count,
            ready_paper_count,
            discovery_status,
            checked_at
        FROM catalog_family_coverage
        WHERE upper(instrument_type) IN ('ACCIONES','CEDEARS')
        ORDER BY instrument_type
    """) if bg._table("catalog_family_coverage") else []

    catalog = bg._rows("""
        SELECT
            ticker,
            instrument_type,
            market,
            currency,
            settlement,
            status,
            capability,
            last_seen_at
        FROM financial_instrument_catalog
        WHERE status='AVAILABLE'
          AND upper(instrument_type) IN ('ACCIONES','CEDEARS')
        ORDER BY instrument_type,ticker,market,currency,settlement
    """) if bg._table("financial_instrument_catalog") else []

    market = bg._rows("""
        SELECT
            symbol,
            asset_class,
            market,
            currency,
            settlement,
            COUNT(*) snapshots,
            MAX(observed_at) last_observed
        FROM market_snapshots
        WHERE upper(asset_class) IN ('ACCIONES','CEDEARS','ACCION','CEDEAR')
        GROUP BY
            symbol,
            asset_class,
            market,
            currency,
            settlement
    """) if bg._table("market_snapshots") else []

    positions = bg._rows("""
        SELECT
            symbol,
            asset_class,
            market,
            currency,
            settlement,
            COUNT(*) positions,
            SUM(CASE WHEN status='OPEN' THEN 1 ELSE 0 END) opened,
            SUM(CASE WHEN status='CLOSED' THEN 1 ELSE 0 END) closed,
            MAX(opened_at) last_opened
        FROM paper_positions
        WHERE upper(asset_class) IN ('ACCIONES','CEDEARS','ACCION','CEDEAR')
        GROUP BY
            symbol,
            asset_class,
            market,
            currency,
            settlement
    """) if bg._table("paper_positions") else []

    operations = bg._rows("""
        SELECT
            paper_id,
            symbol,
            asset_class,
            market,
            currency,
            settlement,
            status,
            quantity,
            entry_price,
            opened_at,
            closed_at,
            net_pnl,
            close_reason,
            strategy_version
        FROM paper_positions
        WHERE upper(asset_class) IN ('ACCIONES','CEDEARS','ACCION','CEDEAR')
        ORDER BY julianday(opened_at) DESC,paper_id DESC
    """) if bg._table("paper_positions") else []

    decisions = bg._rows("""
        SELECT
            symbol,
            COUNT(*) decisions
        FROM paper_decisions
        WHERE symbol IN (SELECT ticker FROM financial_instrument_catalog WHERE status='AVAILABLE' AND upper(instrument_type) IN ('ACCIONES','CEDEARS'))
        GROUP BY symbol
    """) if bg._table("paper_decisions") else []

    gates = bg._rows("""
        SELECT
            symbol,
            COUNT(*) evaluations,
            SUM(CASE
                WHEN final_result='BLOCKED' THEN 1
                ELSE 0
            END) blocked,
            SUM(CASE
                WHEN final_result='OPENED_SIMULATED' THEN 1
                ELSE 0
            END) opened
        FROM trade_gate_evaluations
        WHERE symbol IN (SELECT ticker FROM financial_instrument_catalog WHERE status='AVAILABLE' AND upper(instrument_type) IN ('ACCIONES','CEDEARS'))
        GROUP BY symbol
    """) if bg._table("trade_gate_evaluations") else []

    cauciones = bg._rows("""
        SELECT
            paper_id,
            instrument_id,
            currency,
            status,
            principal,
            annual_rate_fraction,
            opened_at,
            maturity_at,
            settled_at
        FROM paper_cauciones
        ORDER BY julianday(opened_at) DESC,paper_id DESC
    """) if bg._table("paper_cauciones") else []

    observer = (
        bg._rows("""
            SELECT
                mode,
                process_state,
                session_state,
                ppi_auth,
                real_orders_sent,
                heartbeat_at
            FROM observer_state
            WHERE id=1
        """)
        or [{}]
    )[0]

    catalog_key = {
        (
            r.get("ticker"),
            r.get("instrument_type"),
            r.get("market"),
            r.get("currency"),
            r.get("settlement"),
        ): r
        for r in catalog
    }

    market_by_key = {
        (
            r.get("symbol"),
            r.get("asset_class"),
            r.get("market"),
            r.get("currency"),
            r.get("settlement"),
        ): r
        for r in market
    }

    positions_by_key = {
        (
            r.get("symbol"),
            r.get("asset_class"),
            r.get("market"),
            r.get("currency"),
            r.get("settlement"),
        ): r
        for r in positions
    }

    decisions_by_symbol = {
        r.get("symbol"): r
        for r in decisions
    }

    gates_by_symbol = {
        r.get("symbol"): r
        for r in gates
    }

    symbol_families = defaultdict(set)

    for row in catalog:
        symbol_families[
            row.get("ticker")
        ].add(
            row.get("instrument_type")
        )

    family_decisions = defaultdict(int)

    for row in decisions:
        families = symbol_families.get(
            row.get("symbol"),
            set(),
        )

        family = (
            next(iter(families))
            if len(families) == 1
            else "AMBIGUO/SIN_MAPEAR"
        )

        family_decisions[family] += _int(
            row.get("decisions")
        )

    family_gates = defaultdict(
        lambda: {
            "evaluations": 0,
            "blocked": 0,
            "opened": 0,
        }
    )

    for row in gates:
        families = symbol_families.get(
            row.get("symbol"),
            set(),
        )

        family = (
            next(iter(families))
            if len(families) == 1
            else "AMBIGUO/SIN_MAPEAR"
        )

        family_gates[family]["evaluations"] += _int(
            row.get("evaluations")
        )

        family_gates[family]["blocked"] += _int(
            row.get("blocked")
        )

        family_gates[family]["opened"] += _int(
            row.get("opened")
        )

    family_market = defaultdict(
        lambda: {
            "identities": 0,
            "snapshots": 0,
            "last": None,
        }
    )

    for row in market:
        fam = row.get("asset_class") or "SIN_CLASE"

        family_market[fam]["identities"] += 1

        family_market[fam]["snapshots"] += _int(
            row.get("snapshots")
        )

        last = row.get("last_observed")

        if last and (
            family_market[fam]["last"] is None
            or str(last) > str(family_market[fam]["last"])
        ):
            family_market[fam]["last"] = last

    family_positions = defaultdict(
        lambda: {
            "positions": 0,
            "opened": 0,
            "closed": 0,
        }
    )

    for row in positions:
        fam = row.get("asset_class") or "SIN_CLASE"

        family_positions[fam]["positions"] += _int(
            row.get("positions")
        )

        family_positions[fam]["opened"] += _int(
            row.get("opened")
        )

        family_positions[fam]["closed"] += _int(
            row.get("closed")
        )

    family_caps = defaultdict(Counter)

    for row in catalog:
        family_caps[
            row.get("instrument_type")
        ][
            row.get("capability") or "UNKNOWN"
        ] += 1

    coverage_by_family = {
        r.get("instrument_type"): r
        for r in coverage
    }

    families = sorted(
        set(coverage_by_family)
        | set(family_market)
        | set(family_positions)
        | set(family_caps)
    )

    total_catalog = len(catalog)

    total_ready = sum(
        1
        for r in catalog
        if r.get("capability") == "READY_PAPER_SPOT"
    )

    total_market = len(market)

    total_positions = len(operations)

    total_open = sum(
        1
        for r in operations
        if str(r.get("status")).upper() == "OPEN"
    )

    total_closed = sum(
        1
        for r in operations
        if str(r.get("status")).upper() == "CLOSED"
    )

    real_orders = _int(
        observer.get("real_orders_sent")
    )

    cards = "".join((
        bg._card(
            "Familias",
            len(families),
            "Declaradas, observadas o con actividad",
            "green" if families else "gray",
        ),
        bg._card(
            "Catálogo PPI",
            total_catalog,
            "Identidades AVAILABLE encontradas",
            "green" if total_catalog else "yellow",
        ),
        bg._card(
            "READY PAPER",
            total_ready,
            "Contratos habilitados para simulación",
            "green" if total_ready else "yellow",
        ),
        bg._card(
            "Observadas en mercado",
            total_market,
            "Identidades con snapshots guardados",
            "green" if total_market else "yellow",
        ),
        bg._card(
            "Operaciones PAPER",
            total_positions,
            f"{total_open} abiertas / {total_closed} cerradas",
            "green",
        ),
        bg._card(
            "Órdenes reales",
            real_orders,
            "Invariante permanente: debe ser cero",
            "green" if real_orders == 0 else "red",
        ),
    ))

    family_rows = []

    for family in families:
        cov = coverage_by_family.get(
            family,
            {},
        )

        mkt = family_market.get(
            family,
            {},
        )

        pos = family_positions.get(
            family,
            {},
        )

        gate = family_gates.get(
            family,
            {},
        )

        observed = _int(
            cov.get("observed_count")
        )

        ready = _int(
            cov.get("ready_paper_count")
        )

        market_seen = _int(
            mkt.get("identities")
        )

        pos_count = _int(
            pos.get("positions")
        )

        state = _family_state(
            observed,
            ready,
            market_seen,
            pos_count,
        )

        state_view = {
            "READY_PAPER": bg._status("OK"),
            "OBSERVED_BLOCKED": bg._status("PENDIENTE"),
            "LEGACY_POSITION": bg._status("AMARILLO"),
            "NO_INSTRUMENTS": bg._status("GRIS"),
        }[state]

        family_rows.append(
            "<tr>"
            f"<td><b>{bg._e(family)}</b></td>"
            f"<td>{bg._e(cov.get('declared'))}</td>"
            f"<td>{observed}</td>"
            f"<td>{ready}</td>"
            f"<td>{market_seen}</td>"
            f"<td>{_int(mkt.get('snapshots'))}</td>"
            f"<td>{_int(family_decisions.get(family))}</td>"
            f"<td>{_int(gate.get('evaluations'))}</td>"
            f"<td>{_int(gate.get('opened'))}</td>"
            f"<td>{_int(gate.get('blocked'))}</td>"
            f"<td>{pos_count} / "
            f"{_int(pos.get('opened'))} / "
            f"{_int(pos.get('closed'))}</td>"
            f"<td>{bg._e(_capability_text(family_caps.get(family)))}</td>"
            f"<td>{bg._e(cov.get('discovery_status') or 'SIN_CATALOGO')}</td>"
            f"<td>{state_view}</td>"
            "</tr>"
        )

    instrument_rows = []

    for row in catalog:
        key = (
            row.get("ticker"),
            row.get("instrument_type"),
            row.get("market"),
            row.get("currency"),
            row.get("settlement"),
        )

        mkt = market_by_key.get(
            key,
            {},
        )

        pos = positions_by_key.get(
            key,
            {},
        )

        decision = decisions_by_symbol.get(
            row.get("ticker"),
            {},
        )

        gate = gates_by_symbol.get(
            row.get("ticker"),
            {},
        )

        capability = (
            row.get("capability")
            or "UNKNOWN"
        )

        state = (
            "OPERABLE_PAPER"
            if capability == "READY_PAPER_SPOT"
            else capability
        )

        instrument_rows.append(
            "<tr>"
            f"<td><b>{bg._e(row.get('ticker'))}</b></td>"
            f"<td>{bg._e(row.get('instrument_type'))}</td>"
            f"<td>{bg._e(row.get('market'))}</td>"
            f"<td>{bg._e(row.get('currency'))}</td>"
            f"<td>{bg._e(row.get('settlement'))}</td>"
            f"<td>{bg._e(state)}</td>"
            f"<td>{_int(mkt.get('snapshots'))}</td>"
            f"<td>{bg._local_time(mkt.get('last_observed'))}</td>"
            f"<td>{_int(decision.get('decisions'))}</td>"
            f"<td>{_int(gate.get('evaluations'))}</td>"
            f"<td>{_int(pos.get('positions'))} / "
            f"{_int(pos.get('opened'))} / "
            f"{_int(pos.get('closed'))}</td>"
            "</tr>"
        )

    orphan_market = [
        row
        for key, row in market_by_key.items()
        if key not in catalog_key
    ]

    today = datetime.now(bg.TZ).date()
    sessions = []
    cursor = today
    for _ in range(40):
        if es_dia_habil_operativo(cursor):
            sessions.append(cursor)
            if len(sessions) == 5:
                break
        cursor -= timedelta(days=1)
    cutoff = min(sessions) if sessions else today

    def _is_recent_orphan(row):
        try:
            observed = bg.aware_datetime(row.get("last_observed")).astimezone(bg.TZ).date()
            return observed >= cutoff
        except (ValueError, TypeError):
            return False

    recent_orphans = [row for row in orphan_market if _is_recent_orphan(row)]
    archived_orphans = [row for row in orphan_market if not _is_recent_orphan(row)]

    def _orphan_rows(rows):
        return "".join(
            "<tr>"
            f"<td><b>{bg._e(r.get('symbol'))}</b></td>"
            f"<td>{bg._e(r.get('asset_class'))}</td>"
            f"<td>{bg._e(r.get('market'))}</td>"
            f"<td>{bg._e(r.get('currency'))}</td>"
            f"<td>{bg._e(r.get('settlement'))}</td>"
            f"<td>{_int(r.get('snapshots'))}</td>"
            f"<td>{bg._local_time(r.get('last_observed'))}</td>"
            "</tr>"
            for r in rows
        ) or "<tr><td colspan='7'>Sin observaciones en este grupo.</td></tr>"

    orphan_rows = _orphan_rows(recent_orphans)
    orphan_archive_rows = _orphan_rows(archived_orphans)

    operation_rows = "".join(
        "<tr>"
        f"<td>{bg._local_time(r.get('opened_at'))}</td>"
        f"<td><b>{bg._e(r.get('symbol'))}</b></td>"
        f"<td>{bg._e(r.get('asset_class'))}</td>"
        f"<td>{bg._e(r.get('currency'))}</td>"
        f"<td>{bg._e(r.get('settlement'))}</td>"
        f"<td>{bg._status(r.get('status'))}</td>"
        f"<td>{bg._e(r.get('quantity'))}</td>"
        f"<td>{bg._amount(r.get('entry_price'), r.get('currency'))}</td>"
        f"<td>{bg._amount(r.get('net_pnl'), r.get('currency'))}</td>"
        f"<td>{bg._e(r.get('close_reason'))}</td>"
        f"<td>{bg._e(r.get('strategy_version'))}</td>"
        "</tr>"
        for r in operations
    ) or (
        "<tr><td colspan='11'>"
        "Todavía no hay operaciones PAPER."
        "</td></tr>"
    )

    caucion_rows = "".join(
        "<tr>"
        f"<td>{bg._local_time(r.get('opened_at'))}</td>"
        f"<td><b>{bg._e(r.get('instrument_id'))}</b></td>"
        f"<td>{bg._e(r.get('currency'))}</td>"
        f"<td>{bg._status(r.get('status'))}</td>"
        f"<td>{bg._amount(r.get('principal'), r.get('currency'))}</td>"
        f"<td>{bg._e(r.get('annual_rate_fraction'))}</td>"
        f"<td>{bg._local_time(r.get('maturity_at'))}</td>"
        "</tr>"
        for r in cauciones
    ) or (
        "<tr><td colspan='7'>"
        "No hay cauciones PAPER registradas."
        "</td></tr>"
    )

    body = (
        "<h1>Universo operativo — acciones y CEDEARs</h1>"

        "<div class='paper-notice'>"
        "<b>Alcance operativo actual: acciones y CEDEARs.</b> Bonos, cauciones, opciones, futuros y demás familias están deshabilitados por alcance y no se procesan aquí. <b>Lectura única.</b> Esta página reúne catálogo PPI, "
        "observación real de mercado, capacidad contractual, "
        "decisiones, gates y ledger PAPER. "
        "<b>Observado no significa operable:</b> una familia puede "
        "tener cotizaciones y quedar bloqueada hasta confirmar "
        "nominales, margen, vencimiento u otras condiciones."
        "</div>"

        f"<div class='paper-grid'>{cards}</div>"

        "<div class='paper-card'>"
        "<h2>Matriz completa por familia</h2>"
        "<table class='paper-table'>"
        "<tr>"
        "<th>Familia</th>"
        "<th>PPI declara</th>"
        "<th>Catálogo</th>"
        "<th>READY</th>"
        "<th>Mercado</th>"
        "<th>Snapshots</th>"
        "<th>Decisiones</th>"
        "<th>Gates</th>"
        "<th>Aperturas gate</th>"
        "<th>Bloqueos gate</th>"
        "<th>Posiciones total/abiertas/cerradas</th>"
        "<th>Capacidad / bloqueo</th>"
        "<th>Descubrimiento</th>"
        "<th>Estado</th>"
        "</tr>"
        + "".join(family_rows)
        + "</table></div>"

        "<div class='paper-card'>"
        "<h2>Todos los instrumentos del catálogo actual</h2>"
        "<p class='paper-muted'>"
        "Una fila por identidad instrumento/mercado/moneda/plazo. "
        "La columna Estado explica por qué puede o no llegar "
        "a una operación PAPER."
        "</p>"
        "<table class='paper-table'>"
        "<tr>"
        "<th>Ticker</th>"
        "<th>Familia</th>"
        "<th>Mercado</th>"
        "<th>Moneda</th>"
        "<th>Plazo</th>"
        "<th>Estado contractual</th>"
        "<th>Snapshots</th>"
        "<th>Última observación</th>"
        "<th>Decisiones</th>"
        "<th>Gates del símbolo</th>"
        "<th>Posiciones total/abiertas/cerradas</th>"
        "</tr>"
        + "".join(instrument_rows)
        + "</table></div>"

        "<div class='paper-card'>"
        "<h2>Observaciones recientes fuera del catálogo actual (no READY)</h2>"
        "<p class='paper-muted'>"
        "Sirve para detectar legado, catálogo stale o diferencias "
        "entre descubrimiento y observación. "
        "No se interpreta automáticamente como error."
        "</p>"
        "<table class='paper-table'>"
        "<tr>"
        "<th>Ticker</th>"
        "<th>Familia</th>"
        "<th>Mercado</th>"
        "<th>Moneda</th>"
        "<th>Plazo</th>"
        "<th>Snapshots</th>"
        "<th>Última observación</th>"
        "</tr>"
        + orphan_rows
        + "</table>"
        + f"<details><summary class='paper-action'>Ver evidencia histórica fuera de vigencia ({len(archived_orphans)})</summary>"
        + "<table class='paper-table'><tr><th>Ticker</th><th>Familia</th><th>Mercado</th><th>Moneda</th><th>Plazo</th><th>Snapshots</th><th>Última observación</th></tr>"
        + orphan_archive_rows + "</table></details></div>"

        "<div class='paper-card'>"
        "<h2>Todas las operaciones spot PAPER</h2>"
        "<table class='paper-table'>"
        "<tr>"
        "<th>Apertura</th>"
        "<th>Ticker</th>"
        "<th>Familia</th>"
        "<th>Moneda</th>"
        "<th>Plazo</th>"
        "<th>Estado</th>"
        "<th>Cantidad</th>"
        "<th>Entrada</th>"
        "<th>PnL neto</th>"
        "<th>Motivo cierre</th>"
        "<th>Estrategia</th>"
        "</tr>"
        + operation_rows
        + "</table></div>"

        "<div class='paper-card'>"
        "<h2>Todas las cauciones PAPER</h2>"
        "<table class='paper-table'>"
        "<tr>"
        "<th>Apertura</th>"
        "<th>Instrumento</th>"
        "<th>Moneda</th>"
        "<th>Estado</th>"
        "<th>Capital</th>"
        "<th>Tasa anual</th>"
        "<th>Vencimiento</th>"
        "</tr>"
        + caucion_rows
        + "</table></div>"

        "<div class='paper-notice'>"
        f"<b>Runtime:</b> {bg._e(observer.get('mode'))} · "
        f"{bg._e(observer.get('process_state'))}/"
        f"{bg._e(observer.get('session_state'))} · "
        f"PPI {bg._e(observer.get('ppi_auth'))} · "
        f"órdenes reales {real_orders}."
        "</div>"
    )

    return bg._document(
        "Universo operativo",
        body,
        refresh=60,
    )


def _install_nav():
    original = bg._nav

    if getattr(
        original,
        "_porota_universe_wrapped",
        False,
    ):
        return

    def wrapped():
        value = original()

        if "/universo-operativo" in value:
            return value

        anchor = (
            "<a href='/scalping'>"
            "Scalping"
            "</a>"
        )

        link = (
            "<a href='/universo-operativo'>"
            "Universo operativo"
            "</a>"
        )

        return value.replace(
            anchor,
            link + anchor,
        )

    wrapped._porota_universe_wrapped = True
    bg._nav = wrapped


def install(app, check_auth):
    global _installed

    if _installed:
        return

    _installed = True

    _install_nav()

    @app.get(
        "/universo-operativo",
        response_class=HTMLResponse,
    )
    def universo_operativo(
        request: Request,
        token: str = Query(default=""),
        authorization: str | None = Header(default=None),
    ):
        bg._authorize(
            check_auth,
            request,
            token,
            authorization,
        )

        return HTMLResponse(
            _page()
        )
