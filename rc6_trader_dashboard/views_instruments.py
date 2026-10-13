"""An exact identity drill-down; source evidence stays independently attributed."""
from .components import fields, definition_list, notice, e
from .datasets import contracts, instrument_market
from .view_common import render_table, CATALOG, CATALOG_DETAIL, SOURCE_DETAIL
from .views_trading import FAMILY_DETAILS


def family_fields(family):
    from .views_trading import FAMILY_GROUPS
    return next((FAMILY_DETAILS[key] for key, values in FAMILY_GROUPS.items() if family in values), ())


def render(p, destination, tab):
    if tab in {"buscador", "ficha"}:
        page = p.catalog()
        if tab == "ficha" and p.filters.get("identity") and len(page.rows) == 1:
            row = page.rows[0]
            return "<section class='panel'><h2>Identidad canónica</h2>" + definition_list(row, (*CATALOG, *CATALOG_DETAIL, *family_fields(row["family"]))) + "</section>"
        return render_table(p, destination, tab, page, "Buscar identidad exacta", CATALOG, CATALOG_DETAIL,
                            filters=True, catalog=True, note="Identidad: ticker + familia + mercado + moneda + settlement. Complementos no reemplazan la identidad PPI.")
    if tab == "mercado":
        return render_table(p, destination, tab, instrument_market(p), "Mercado & Liquidez · identidad exacta",
                            fields("symbol|Ticker;last|Último|money;freshness|Freshness|status;bid|Bid|money;ask|Ask|money"),
                            fields("bid_size|Bid depth|number;ask_size|Ask depth|number;spread|Spread;trade_at|Último negocio|time;provider_clock|Reloj provider|time;receipt_clock|Reloj captura|time;historical_activity|Actividad histórica;source|Fuente"),
                            note="Seleccione una identidad completa desde Buscador. El reloj de captura no reemplaza el reloj del proveedor.")
    page = contracts(p)
    columns = fields("symbol|Ticker;source_class|Fuente contractual;state|Evidencia|status;observed_at|Observado|time")
    if page.reason == "SELECT_EXACT_IDENTITY_FOR_CONTRACT":
        columns = CATALOG
    detail = fields("contract|Campos family-specific con provenance;evidence_hash|Hash de evidencia;snapshot_id|Snapshot;conflicts|Conflictos;freshness|Freshness|status")
    if page.rows and page.reason != "SELECT_EXACT_IDENTITY_FOR_CONTRACT":
        detail = (*family_fields(page.rows[0].get("family")), *detail)
    return render_table(p, destination, tab, page, "Contrato" if tab == "contrato" else "Evidencia por fuente", columns, detail,
                        note="PPI primary / IOL complement / fuentes públicas: cada evidencia conserva fuente y reloj. Seleccione identidad exacta para consultar contrato; LKG no se promueve a LIVE.")
