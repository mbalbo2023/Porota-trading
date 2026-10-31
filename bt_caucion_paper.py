"""Cauciones colocadoras y disponibilidad de caja del simulador v17.

Sin acceso al broker. El capital se inmoviliza y sólo vuelve al vencimiento
contractual. No hay stop, venta de la caución ni reinversión automática.
Los términos normalizados deben estar respaldados por datos del instrumento.
"""

import hashlib
import json
import sqlite3
import uuid
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

from bs_instrument_contracts import aware_datetime, cash_currency, decimal_value
from bl_candle_engine import fingerprint, stamp
import cd_spot_ledger as spot_ledger
from cf_sale_settlement import modeled_sale_settlement, validated_sale_settlement

ZERO = Decimal("0")
CENT = Decimal("0.01")
TZ = ZoneInfo("America/Argentina/Buenos_Aires")
ADMIN_CANCEL_AUTHORITY_A207 = "ISSUE773_A207_PAPER_ACCOUNTING_REVERSAL_20261030"


def offer_payload(offer):
    values = asdict(offer)
    for key, value in values.items():
        if isinstance(value, Decimal):
            values[key] = format(value.normalize(), 'f')
    for key in ('quoted_at','maturity_at'):
        values[key] = stamp(values[key])
    return values


def book_key(offer):
    return fingerprint({k:offer_payload(offer)[k] for k in ('instrument_id','currency','quoted_at')})


def book_payload(offer):
    return {k:v for k,v in offer_payload(offer).items() if k not in {'quoted_total_fees','fee_quote_principal'}}


def money(value):
    return decimal_value(value, "importe").quantize(CENT, rounding=ROUND_HALF_UP)


def init_schema(store):
    with store.connect() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS paper_sale_receivables(
          paper_id TEXT PRIMARY KEY REFERENCES paper_positions(paper_id),
          currency TEXT NOT NULL, net_proceeds TEXT NOT NULL,
          available_at TEXT, basis TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS paper_cauciones(
          paper_id TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE,
          request_fingerprint TEXT NOT NULL, source TEXT NOT NULL,
          instrument_id TEXT NOT NULL, currency TEXT NOT NULL, status TEXT NOT NULL,
          principal TEXT NOT NULL, annual_rate_fraction TEXT NOT NULL,
          interest_days INTEGER NOT NULL, day_count_basis INTEGER NOT NULL,
          gross_interest TEXT NOT NULL, total_fees TEXT NOT NULL,
          fee_payment TEXT NOT NULL, opened_at TEXT NOT NULL, maturity_at TEXT NOT NULL,
          settled_at TEXT, terms_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS paper_caucion_allocations(
          request_id TEXT PRIMARY KEY, request_hash TEXT NOT NULL,
          evaluated_at TEXT NOT NULL, decision_json TEXT NOT NULL,
          paper_id TEXT UNIQUE REFERENCES paper_cauciones(paper_id));
        CREATE TABLE IF NOT EXISTS paper_caucion_admin_cancellations(
          paper_id TEXT PRIMARY KEY REFERENCES paper_cauciones(paper_id),
          cancelled_at TEXT NOT NULL, authority TEXT NOT NULL,
          principal TEXT NOT NULL, total_fees TEXT NOT NULL,
          request_fingerprint TEXT NOT NULL, terms_sha256 TEXT NOT NULL,
          reason TEXT NOT NULL);
        CREATE TRIGGER IF NOT EXISTS paper_caucion_admin_cancel_no_update
          BEFORE UPDATE ON paper_caucion_admin_cancellations
          BEGIN SELECT RAISE(ABORT,'PAPER_CAUCION_ADMIN_RECEIPT_IMMUTABLE'); END;
        CREATE TRIGGER IF NOT EXISTS paper_caucion_admin_cancel_no_delete
          BEFORE DELETE ON paper_caucion_admin_cancellations
          BEGIN SELECT RAISE(ABORT,'PAPER_CAUCION_ADMIN_RECEIPT_IMMUTABLE'); END;
        CREATE TABLE IF NOT EXISTS paper_equity_by_currency(
          id INTEGER PRIMARY KEY AUTOINCREMENT, measured_at TEXT NOT NULL,
          currency TEXT NOT NULL, cash TEXT NOT NULL, exposure TEXT NOT NULL,
          pending_proceeds TEXT NOT NULL, caucion_principal TEXT NOT NULL,
          caucion_accrued TEXT NOT NULL, unrealized_pnl TEXT NOT NULL,
          realized_pnl TEXT NOT NULL, equity TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_paper_equity_currency_latest
          ON paper_equity_by_currency(currency,id DESC);
        """)
        # Migración aditiva. Cada recibo se crea una sola vez, sin reescribir
        # una fecha que luego haya sido conciliada contra una fuente mejor.
        closed = c.execute("""SELECT p.* FROM paper_positions p
          LEFT JOIN paper_sale_receivables r USING(paper_id)
          WHERE p.status='CLOSED' AND r.paper_id IS NULL""").fetchall()
        for p in closed:
            if spot_ledger.sales(c,p['paper_id']):
                continue
            try:
                proceeds = spot_ledger.single_close_proceeds(p,c.execute(
                    "SELECT * FROM paper_fills WHERE paper_id=? AND side='SELL_SIMULATED'",(p['paper_id'],)).fetchall())
            except (ValueError,TypeError,ArithmeticError):
                # Sin evidencia conciliada no crear un recibo. La caja sigue
                # bloqueada, sin impedir supervisar otras posiciones válidas.
                continue
            record_sale(c, p["paper_id"], p["settlement"], p["closed_at"],
                        proceeds,
                        currency=p["currency"])


def record_sale(c, paper_id, settlement, traded_at, net_proceeds, currency="ARS"):
    try:
        available = modeled_sale_settlement(settlement, traded_at)
    except (ValueError, TypeError):
        available = None
    c.execute("INSERT OR IGNORE INTO paper_sale_receivables VALUES(?,?,?,?,?)",
              (paper_id, cash_currency(currency), str(net_proceeds), available,
               "PAPER_CONSERVATIVE_CALENDAR" if available else "PENDING_CONFIRMATION"))


def pending_proceeds(store, as_of, currency="ARS", *, connection=None):
    at = aware_datetime(as_of)
    if connection is None:
        with store.connect() as c:
            c.execute('BEGIN')
            return pending_proceeds(store, at, currency, connection=c)
    currency = cash_currency(currency)
    rows = connection.execute("""SELECT p.*,r.paper_id AS receipt_id,r.currency AS receipt_currency,
        r.net_proceeds,r.available_at,r.basis FROM paper_positions p
        LEFT JOIN paper_sale_receivables r USING(paper_id) WHERE p.currency=? AND p.status='CLOSED'""",
        (cash_currency(currency),)).fetchall()
    pending = spot_ledger.pending(connection,at,currency)
    for row in rows:
        if spot_ledger.sales(connection,row['paper_id']):
            continue
        if aware_datetime(row['closed_at']) > at:
            continue
        if row['receipt_id'] is None or row['receipt_currency'] != currency:
            raise ValueError('Venta sin recibo de liquidación en su moneda')
        amount = decimal_value(row['net_proceeds'], 'producido de venta')
        expected = spot_ledger.single_close_proceeds(row,connection.execute(
            "SELECT * FROM paper_fills WHERE paper_id=? AND side='SELL_SIMULATED'",(row['paper_id'],)).fetchall())
        if amount != expected:
            raise ValueError('Producido del recibo no concilia con el fill')
        available = validated_sale_settlement(row['settlement'],row['closed_at'],
                                             row['available_at'],row['basis'])
        if amount > 0 and (available is None or available > at):
            pending += amount
    return pending


@dataclass(frozen=True)
class CaucionOffer:
    instrument_id: str
    currency: str
    annual_rate_fraction: Decimal
    start_date: str
    maturity_at: str
    quoted_at: str
    available_principal: Decimal
    minimum_principal: Decimal
    principal_step: Decimal
    day_count_basis: int
    fee_payment: str  # UPFRONT o MATURITY; no inferirlo del ticker.
    metadata_source: str
    # Costo total del contrato, no una tasa. None usa el modelo heredado en ARS.
    quoted_total_fees: Decimal | None = None
    fee_quote_principal: Decimal | None = None
    # PAPER policy terms are not broker terms.  They are accepted only as an
    # explicitly named conservative cap with versioned fee provenance.
    paper_fill_policy: str | None = None
    fee_authority: str | None = None

    def __post_init__(self):
        if not self.instrument_id.strip() or not self.metadata_source.strip():
            raise ValueError("Falta identificación o fuente del contrato de caución")
        object.__setattr__(self, "currency", cash_currency(self.currency))
        if self.fee_payment not in {"UPFRONT", "MATURITY"}:
            raise ValueError("Falta momento de cobro de los costos")
        if self.day_count_basis not in {360, 365}:
            raise ValueError("Base anual no soportada; debe confirmarse con el contrato")
        for field in ("annual_rate_fraction", "available_principal", "minimum_principal", "principal_step"):
            value = decimal_value(getattr(self, field), field, nonnegative=True,
                                  positive=field != "annual_rate_fraction")
            object.__setattr__(self, field, value)
        if self.quoted_total_fees is not None:
            object.__setattr__(self, "quoted_total_fees", decimal_value(self.quoted_total_fees, "costos cotizados", nonnegative=True))
            object.__setattr__(self, "fee_quote_principal", decimal_value(self.fee_quote_principal, "capital del presupuesto", positive=True))
        if self.paper_fill_policy not in {None, "CONSERVATIVE_NOTIONAL_CAP", "LIVE_PPI_BID_PARTICIPATION_CAP"}:
            raise ValueError("Política PAPER de caución no soportada")
        if (self.paper_fill_policy is not None
                and not (self.metadata_source.startswith("CONTRACT_EVIDENCE_V2:")
                         or self.metadata_source.startswith("PPI_PRIMARY_LIVE_BOOK+PAPER_POLICY:"))):
            raise ValueError("Falta provenance v2 o PPI live+policy verificable de la política PAPER de caución")
        if self.fee_authority is not None:
            from au_fee_schedule import CAUCION_PAPER_FEE_AUTHORITY
            if (self.currency != "ARS" or self.quoted_total_fees is not None
                    or self.paper_fill_policy not in {"CONSERVATIVE_NOTIONAL_CAP", "LIVE_PPI_BID_PARTICIPATION_CAP"}
                    or self.fee_authority != CAUCION_PAPER_FEE_AUTHORITY):
                raise ValueError("Autoridad de costos PAPER incompatible")
        if self.currency != "ARS" and self.quoted_total_fees is None:
            raise ValueError("Caución en moneda extranjera requiere costos explícitos en esa moneda/plaza")
        start = date.fromisoformat(self.start_date)
        maturity = aware_datetime(self.maturity_at, "vencimiento").astimezone(TZ)
        aware_datetime(self.quoted_at, "cotización")
        if maturity.date() <= start:
            raise ValueError("El vencimiento debe ser posterior a la fecha de inicio")

    @property
    def interest_days(self):
        # Días corridos reales entre fechas; un viernes a lunes devenga 3,
        # aunque el mercado identifique el plazo como un día hábil.
        return (aware_datetime(self.maturity_at).astimezone(TZ).date() - date.fromisoformat(self.start_date)).days

    def economics(self, principal):
        principal = decimal_value(principal, "capital", positive=True)
        if principal != money(principal) or principal % self.principal_step:
            raise ValueError("Capital incompatible con centavos o paso mínimo")
        if not self.minimum_principal <= principal <= self.available_principal:
            raise ValueError("Capital fuera del mínimo o de la profundidad disponible")
        interest = money(principal * self.annual_rate_fraction * self.interest_days / self.day_count_basis)
        if self.quoted_total_fees is not None:
            if principal != self.fee_quote_principal:
                raise ValueError("El costo presupuestado corresponde a otro capital")
            fees = money(self.quoted_total_fees)
        else:
            import au_fee_schedule as tariff
            fee = tariff.arancel_de("CAUCIONES")
            vat = decimal_value(tariff.IVA_PCT, "IVA", nonnegative=True)
            commission = decimal_value(fee.comision, "comisión anual", nonnegative=True)
            rights = decimal_value(fee.derecho, "derechos anuales", nonnegative=True)
            rate = commission * (1 + vat if fee.comision_iva else 1)
            rate += rights * (1 + vat if fee.derecho_iva else 1)
            # El arancel heredado define base 365. La tasa de interés puede
            # tener otra base: no trasladar una convención a la otra.
            fees = money(principal * rate * self.interest_days / 365)
        return interest, fees, interest - fees


def automatic_liquidity_cap(offer, participation):
    """Return the applicable cap without relabelling PAPER policy as depth."""
    participation = decimal_value(participation, "participación", positive=True)
    if participation > 1:
        raise ValueError("Participación fuera de rango")
    if offer.paper_fill_policy == "CONSERVATIVE_NOTIONAL_CAP":
        return offer.available_principal
    # Exact live PPI depth stays provider evidence; PAPER participates
    # only up to the configured conservative fraction.
    return offer.available_principal * participation


def _runtime_caucion_market_error(at):
    """Conservative *internal PAPER* market floor, not a broker cutoff."""
    from ak_byma_calendar import es_dia_habil_operativo
    from co_market_sessions_hf6 import BYMA_PAPER_SPOT_OPEN, BYMA_PAPER_SPOT_CLOSE

    local = aware_datetime(at).astimezone(TZ)
    if not es_dia_habil_operativo(local.date()):
        return "CAUCION_RUNTIME_MARKET_CLOSED_OR_CALENDAR_UNKNOWN"
    clock = local.time().replace(tzinfo=None)
    cutoff = (datetime.combine(local.date(), BYMA_PAPER_SPOT_CLOSE)
              - timedelta(minutes=5)).time()
    if not BYMA_PAPER_SPOT_OPEN <= clock < cutoff:
        return "CAUCION_RUNTIME_OUTSIDE_CONSERVATIVE_MARKET_WINDOW"
    return ""


def _runtime_caucion_book_error(store, connection, offer, at):
    """Rebind live PPI BID, exact catalog/policy and original quote under lock.

    An offer's ``metadata_source`` string proves nothing by itself. The
    canonical collector persists matching current+history book rows; its
    pure, local adapter reconstructs the terms from that exact PPI source.
    No network call, real order, or historical synthetic fixture is involved.
    """
    from au_fee_schedule import CAUCION_PAPER_FEE_AUTHORITY
    from bu_instrument_catalog import (lookup, _candidate_has_ppi_primary,
                                       _candidate_timestamp_is_fresh)
    from di_caucion_cash_sweep_runtime_hf6 import offers_from_store
    from dj_caucion_live_ppi_rc6 import SOURCE as PPI_LIVE_SOURCE

    if (offer.paper_fill_policy != "LIVE_PPI_BID_PARTICIPATION_CAP"
            or offer.fee_authority != CAUCION_PAPER_FEE_AUTHORITY
            or offer.currency != "ARS"
            or not offer.metadata_source.startswith(
                "PPI_PRIMARY_LIVE_BOOK+PAPER_POLICY:v1;")):
        return "CAUCION_RUNTIME_PPI_LIVE_CONTRACT_REQUIRED"
    try:
        reconstructed, _ = offers_from_store(store, now=at)
        matching = [item for item in reconstructed
                    if item.instrument_id == offer.instrument_id and item.currency == offer.currency]
        if len(matching) != 1 or asdict(matching[0]) != asdict(offer):
            return "CAUCION_RUNTIME_PPI_LIVE_BOOK_OR_TERMS_UNVERIFIED"
        source_rows = connection.execute("""SELECT f.market,f.settlement,f.settlement_source,
                f.last_seen_at,f.metadata_json,b.quoted_at,b.tna_fraction,
                b.available_principal,b.term_days,b.state,b.source,b.observed_at
            FROM financial_instrument_catalog f JOIN paper_caucion_live_book b
              ON b.ticker=f.ticker AND b.market=f.market
             AND b.currency=f.currency AND b.settlement=f.settlement
            WHERE f.ticker=? AND f.instrument_type='CAUCIONES' AND f.currency='ARS'
              AND f.market='BYMA' AND f.status='AVAILABLE'
              AND f.capability='READY_PAPER_CAUCION_PLACING'""",
            (offer.instrument_id,)).fetchall()
        valid = []
        for row in source_rows:
            if (row["source"] != PPI_LIVE_SOURCE or row["state"] != "BOOK_READY"
                    or not _candidate_has_ppi_primary(row["settlement_source"],
                                                      row["metadata_json"])
                    or not _candidate_timestamp_is_fresh(row["last_seen_at"],
                                                         aware_datetime(at).isoformat(), 86400)):
                continue
            quote_at = aware_datetime(row["quoted_at"], "book caución")
            received_at = aware_datetime(row["observed_at"], "recepción book caución")
            age = (aware_datetime(at) - received_at).total_seconds()
            if not (quote_at <= received_at <= aware_datetime(at) and 0 <= age <= 90):
                continue
            # The collector writes a historical receipt alongside the current
            # book. Reject an isolated current row or a mismatching revision.
            receipt = connection.execute("""SELECT quoted_at,tna_fraction,
                    available_principal,term_days,state,source,observed_at
                FROM paper_caucion_live_book_history
                WHERE ticker=? AND market=? AND currency='ARS' AND settlement=?
                ORDER BY id DESC LIMIT 1""",
                (offer.instrument_id, row["market"], row["settlement"])).fetchone()
            if (receipt is None or any(receipt[field] != row[field] for field in (
                    "quoted_at", "tna_fraction", "available_principal", "term_days",
                    "state", "source", "observed_at"))):
                continue
            contract = lookup(store, offer.instrument_id, "CAUCIONES", row["settlement"],
                              market=row["market"], currency="ARS")
            if (not contract or contract.get("_admission_reason")
                    or contract.get("capability") != "READY_PAPER_CAUCION_PLACING"):
                continue
            valid.append(row)
        return "" if len(valid) == 1 else "CAUCION_RUNTIME_PPI_IDENTITY_OR_RECEIPT_UNVERIFIED"
    except (ValueError, TypeError, KeyError, AttributeError, sqlite3.Error):
        return "CAUCION_RUNTIME_PPI_LIVE_BOOK_OR_TERMS_UNVERIFIED"


def validate_caucion_admin_receipt(p, receipt):
    """Require a sealed, immutable journal before accepting a canceled row."""
    if receipt is None or p['status'] != 'CANCELLED_ADMIN':
        raise ValueError('CAUCION_ADMIN_RECEIPT_REQUIRED')
    cancelled = aware_datetime(receipt['cancelled_at'], 'cancelación caución')
    opened = aware_datetime(p['opened_at'], 'apertura caución')
    if (receipt['paper_id'] != p['paper_id']
            or receipt['authority'] != ADMIN_CANCEL_AUTHORITY_A207
            or receipt['reason'] != 'ADMIN_CANCEL_NO_MARKET_EXECUTION'
            or receipt['principal'] != p['principal']
            or receipt['total_fees'] != p['total_fees']
            or receipt['request_fingerprint'] != p['request_fingerprint']
            or receipt['terms_sha256'] != hashlib.sha256(p['terms_json'].encode()).hexdigest()
            or cancelled < opened or p['settled_at'] is not None):
        raise ValueError('CAUCION_ADMIN_RECEIPT_MISMATCH')
    return cancelled


def cancel_open_caucion_admin(connection, paper_id, at, authority):
    """Neutralize one unliquidated PAPER placement without fabricating yield.

    This is a caller-owned BEGIN IMMEDIATE operation; the deploy coordinator
    verifies an exact frozen snapshot, exclusive host lock and stopped Docker
    before invoking it. Allocation/contract terms remain immutable.
    """
    if authority != ADMIN_CANCEL_AUTHORITY_A207 or not connection.in_transaction:
        raise ValueError('CAUCION_ADMIN_AUTHORITY_OR_TRANSACTION_REQUIRED')
    at = aware_datetime(at)
    row = connection.execute('SELECT * FROM paper_cauciones WHERE paper_id=?',
                             (str(paper_id),)).fetchone()
    if row is None or row['status'] != 'OPEN' or row['settled_at'] is not None:
        raise ValueError('CAUCION_ADMIN_UNSETTLED_OPEN_REQUIRED')
    validate_position(row)
    if at < aware_datetime(row['opened_at']):
        raise ValueError('CAUCION_ADMIN_CLOCK_BEFORE_PLACEMENT')
    if connection.execute('''SELECT 1 FROM paper_events WHERE paper_id=?
        AND event_type IN ('PAPER_CAUCION_MATURED','PAPER_CAUCION_ADMIN_CANCELLED_NO_EXECUTION')
        LIMIT 1''', (str(paper_id),)).fetchone():
        raise ValueError('CAUCION_ADMIN_SETTLEMENT_EVENT_PRESENT')
    original_events = connection.execute('''SELECT * FROM paper_events
        WHERE paper_id=? ORDER BY id''', (str(paper_id),)).fetchall()
    if (len(original_events) != 1
            or original_events[0]['event_type'] != 'PAPER_CAUCION_PLACED'
            or original_events[0]['source'] != 'PRODUCTION_PAPER'
            or aware_datetime(original_events[0]['event_at']) != aware_datetime(row['opened_at'])):
        raise ValueError('CAUCION_ADMIN_ORIGINAL_PLACEMENT_EVENT_REQUIRED')
    allocation = connection.execute('''SELECT * FROM paper_caucion_allocations
        WHERE paper_id=?''', (str(paper_id),)).fetchone()
    if allocation is not None:
        try:
            from cb_caucion_audit import _validated
            decision, placement = _validated(allocation, connection)
        except (TypeError, ValueError, KeyError, sqlite3.Error) as exc:
            raise ValueError('CAUCION_ADMIN_ALLOCATION_INVALID') from exc
        if (decision.get('status') != 'PLACED_SIMULATED'
                or placement is None or placement['paper_id'] != str(paper_id)):
            raise ValueError('CAUCION_ADMIN_ALLOCATION_NOT_PLACED')
    connection.execute('''CREATE TABLE IF NOT EXISTS paper_caucion_admin_cancellations(
      paper_id TEXT PRIMARY KEY REFERENCES paper_cauciones(paper_id),
      cancelled_at TEXT NOT NULL, authority TEXT NOT NULL,
      principal TEXT NOT NULL, total_fees TEXT NOT NULL,
      request_fingerprint TEXT NOT NULL, terms_sha256 TEXT NOT NULL,
      reason TEXT NOT NULL)''')
    connection.execute('''CREATE TRIGGER IF NOT EXISTS paper_caucion_admin_cancel_no_update
      BEFORE UPDATE ON paper_caucion_admin_cancellations
      BEGIN SELECT RAISE(ABORT,'PAPER_CAUCION_ADMIN_RECEIPT_IMMUTABLE'); END''')
    connection.execute('''CREATE TRIGGER IF NOT EXISTS paper_caucion_admin_cancel_no_delete
      BEFORE DELETE ON paper_caucion_admin_cancellations
      BEGIN SELECT RAISE(ABORT,'PAPER_CAUCION_ADMIN_RECEIPT_IMMUTABLE'); END''')
    reason = 'ADMIN_CANCEL_NO_MARKET_EXECUTION'
    connection.execute('''INSERT INTO paper_caucion_admin_cancellations
        VALUES (?,?,?,?,?,?,?,?)''',
        (paper_id, at.isoformat(), authority, row['principal'], row['total_fees'],
         row['request_fingerprint'], hashlib.sha256(row['terms_json'].encode()).hexdigest(),
         reason))
    affected = connection.execute('''UPDATE paper_cauciones SET status='CANCELLED_ADMIN'
        WHERE paper_id=? AND status='OPEN' AND settled_at IS NULL''', (paper_id,)).rowcount
    if affected != 1:
        raise ValueError('CAUCION_ADMIN_CONCURRENT_CHANGE')
    connection.execute('''INSERT INTO paper_events VALUES(NULL,?,?,?,?,?)''',
                       (at.isoformat(), 'PRODUCTION_PAPER',
                        'PAPER_CAUCION_ADMIN_CANCELLED_NO_EXECUTION', str(paper_id),
                        'Reversión administrativa PAPER: colocación sin liquidación verificable'))
    closed = connection.execute('SELECT * FROM paper_cauciones WHERE paper_id=?',
                                (paper_id,)).fetchone()
    receipt = connection.execute('''SELECT * FROM paper_caucion_admin_cancellations
        WHERE paper_id=?''', (paper_id,)).fetchone()
    validate_position(closed, cancel_receipt=receipt)
    return {'status': 'CANCELLED_ADMIN', 'paper_id': str(paper_id),
            'principal_released': row['principal'], 'upfront_fee_reversed':
            row['total_fees'] if row['fee_payment'] == 'UPFRONT' else '0',
            'real_routes_used': []}


def validate_position(p, *, cancel_receipt=None):
    """Concordancia interna antes de reconocer caja, profundidad o un crédito.

    No consulta PPI ni revaloriza comisiones antiguas con el tarifario actual.
    El costo legado ya contabilizado permanece fijo; un presupuesto explícito
    sí debe coincidir. Un registro inválido requiere conciliación, no reparación.
    """
    try:
        offer = CaucionOffer(**json.loads(p['terms_json']))
        principal = decimal_value(p['principal'],'principal',positive=True)
        fees = decimal_value(p['total_fees'],'costos',nonnegative=True)
        interest = decimal_value(p['gross_interest'],'interés',nonnegative=True)
        rate = decimal_value(p['annual_rate_fraction'],'tasa',nonnegative=True)
        if any(value != money(value) for value in (principal,fees,interest)):
            raise ValueError('Importes incompatibles con centavos')
        if (principal % offer.principal_step or
                not offer.minimum_principal <= principal <= offer.available_principal):
            raise ValueError('Capital incompatible con el contrato')
        if (not p['paper_id'] or not p['request_id'] or p['source']!='PRODUCTION_PAPER'
                or p['instrument_id']!=offer.instrument_id or p['currency']!=offer.currency
                or p['fee_payment']!=offer.fee_payment or rate!=offer.annual_rate_fraction
                or p['interest_days']!=offer.interest_days or p['day_count_basis']!=offer.day_count_basis):
            raise ValueError('Términos no concuerdan con el contrato')
        expected = money(principal*offer.annual_rate_fraction*offer.interest_days/offer.day_count_basis)
        if interest!=expected or interest<=fees:
            raise ValueError('Interés o neto incompatible con la colocación')
        if offer.quoted_total_fees is not None and (
                principal!=offer.fee_quote_principal or fees!=money(offer.quoted_total_fees)):
            raise ValueError('Costo no concuerda con el presupuesto exacto')
        digest = hashlib.sha256((p['terms_json']+'|'+format(principal.normalize(),'f')).encode()).hexdigest()
        if digest!=p['request_fingerprint']:
            raise ValueError('Huella de términos/capital inconsistente')
        opened, maturity = map(aware_datetime,(p['opened_at'],p['maturity_at']))
        settled = aware_datetime(p['settled_at']) if p['settled_at'] is not None else None
        cancelled = (validate_caucion_admin_receipt(p, cancel_receipt)
                     if p['status'] == 'CANCELLED_ADMIN' else None)
        if (opened.astimezone(TZ).date().isoformat()!=offer.start_date
                or aware_datetime(offer.quoted_at)>opened or maturity!=aware_datetime(offer.maturity_at)
                or maturity<=opened or p['status'] not in {'OPEN','MATURED','CANCELLED_ADMIN'}
                or (p['status']=='MATURED')!=(settled is not None)
                or (settled is not None and settled<maturity)
                or (cancelled is not None and cancelled < opened)):
            raise ValueError('Cronología de caución inválida')
        return offer
    except (ValueError,TypeError,KeyError,AttributeError,ArithmeticError) as exc:
        raise ValueError('CAUCION_LEDGER_INVALID: '+str(exc)) from exc


def paper_caucion_economic_projection(connection, positions, *, as_of=None):
    """Project booked or separately restated PAPER costs from one DB snapshot.

    A historical BYMA/PPI public cost model is not a broker tariff.  The
    restatement reader proves all six append-only receipts, original ledger
    fingerprints and model seals *before* any caller sees a revised amount.
    A missing, partial or altered restatement fails closed for every consumer.
    No historical row is modified, and no real-order permission is conferred.
    ``connection`` must use sqlite3.Row and an already-established snapshot.
    """
    from scripts.rc6_caucion_fee_restatement_apply import read_verified_restated_fees

    try:
        reference = aware_datetime(as_of) if as_of is not None else None
        restated = read_verified_restated_fees(connection)
        output = []
        for original in positions:
            p = dict(original)
            cancel_receipt = None
            if p['status'] == 'CANCELLED_ADMIN':
                has_table = connection.execute('''SELECT 1 FROM sqlite_master
                    WHERE type='table' AND name='paper_caucion_admin_cancellations' ''').fetchone()
                if not has_table:
                    raise ValueError('CAUCION_ADMIN_RECEIPT_TABLE_REQUIRED')
                cancel_receipt = connection.execute('''SELECT * FROM paper_caucion_admin_cancellations
                    WHERE paper_id=?''', (p['paper_id'],)).fetchone()
                p['admin_cancelled_at'] = validate_caucion_admin_receipt(p, cancel_receipt).isoformat()
                p['admin_cancel_receipt_verified'] = True
            receipt = restated.get(p['paper_id'])
            if receipt is None:
                validate_position(p, cancel_receipt=cancel_receipt)
                p['fee_evidence_scope'] = 'BOOKED_PAPER_CONTRACT'
            else:
                if p['status'] != 'MATURED' or p['currency'] != 'ARS':
                    raise ValueError('RESTATEMENT_UNEXPECTED_POSITION')
                p['original_total_fees'] = p['total_fees']
                known_at = aware_datetime(receipt['known_at_utc'])
                if reference is not None and reference < known_at:
                    # Point-in-time cash/valuation cannot know a later
                    # restatement. This does not erase the sealed receipt.
                    p['fee_evidence_scope'] = 'HISTORICAL_BOOKED_BEFORE_RESTATEMENT'
                else:
                    p['total_fees'] = receipt['effective_total_fees']
                    p['fee_evidence_scope'] = 'PUBLIC_MODEL_PAPER_ONLY_ACCOUNT_UNVERIFIED'
                p['fee_model'] = receipt['model']
                p['fee_known_at_utc'] = receipt['known_at_utc']
                p['broker_account_fees_verified'] = False
            output.append(p)
        return output
    except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
        raise ValueError('CAUCION_LEDGER_INVALID: '+str(exc)) from exc


class CaucionBook:
    def __init__(self, store):
        self.store = store

    def positions(self, *, currency=None, status=None, connection=None, as_of=None):
        if connection is None:
            with self.store.connect() as c:
                # Pin the original rows and all six append-only receipts to
                # one WAL snapshot; implicit per-SELECT reads could otherwise
                # mix generations while a writer commits a restatement.
                c.execute('BEGIN')
                return self.positions(currency=currency, status=status, connection=c, as_of=as_of)
        # External callers sometimes pass an autocommit connection. A bounded
        # read-only SAVEPOINT pins the row set and all six receipts to the
        # same WAL generation without committing someone else's transaction.
        # Validate BEFORE filtering: an altered ARS debit cannot disappear.
        own_snapshot = not connection.in_transaction
        if own_snapshot:
            connection.execute('SAVEPOINT rc6_caucion_fee_snapshot')
        try:
            rows = paper_caucion_economic_projection(
                connection, connection.execute('SELECT * FROM paper_cauciones').fetchall(), as_of=as_of)
        finally:
            if own_snapshot:
                connection.execute('RELEASE SAVEPOINT rc6_caucion_fee_snapshot')
        return [p for p in rows if (currency is None or p['currency']==currency)
                and (status is None or p['status']==status)]

    def used_principal(self, offer, *, connection):
        """La misma fotografía de liquidez no se repone por otro request_id."""
        used = ZERO
        # Validate the entire ledger and all six historical fee receipts in the
        # caller's transaction before considering available depth. A retired
        # PAPER fee authority can only be read as a sealed historical position;
        # it must never become an authority for a new placement.
        for p in self.positions(connection=connection):
            if p['instrument_id'] != offer.instrument_id or p['currency'] != offer.currency:
                continue
            if p.get('fee_evidence_scope') == 'PUBLIC_MODEL_PAPER_ONLY_ACCOUNT_UNVERIFIED':
                from scripts.rc6_caucion_fee_ro_provenance import LEGACY_AUTHORITY
                try:
                    terms = json.loads(p['terms_json'])
                    if (not isinstance(terms, dict) or terms.get('fee_authority') != LEGACY_AUTHORITY
                            or p.get('broker_account_fees_verified') is not False):
                        raise ValueError('HISTORICAL_FEE_SCOPE_MISMATCH')
                    previous = CaucionOffer(**(terms | {'fee_authority': None}))
                except (ValueError, TypeError, KeyError, AttributeError, ArithmeticError) as exc:
                    raise ValueError('CAUCION_LEDGER_INVALID: HISTORICAL_TERMS_INVALID') from exc
            else:
                if p['status'] == 'CANCELLED_ADMIN':
                    if p.get('admin_cancel_receipt_verified') is not True:
                        raise ValueError('CAUCION_ADMIN_RECEIPT_REQUIRED')
                    previous = CaucionOffer(**json.loads(p['terms_json']))
                else:
                    previous = validate_position(p)
            if aware_datetime(previous.quoted_at) > aware_datetime(offer.quoted_at):
                raise ValueError('CAUCION_OLDER_BOOK')
            if book_key(previous) == book_key(offer):
                if book_payload(previous) != book_payload(offer):
                    raise ValueError('CAUCION_CONFLICTING_BOOK')
                if self.state_at(p, aware_datetime(offer.quoted_at)) == 'OPEN':
                    used += decimal_value(p['principal'],'capital consumido',positive=True)
        return used

    @staticmethod
    def state_at(p, at):
        at = aware_datetime(at)
        opened, maturity = map(aware_datetime, (p['opened_at'], p['maturity_at']))
        settled = aware_datetime(p['settled_at']) if p['settled_at'] else None
        cancelled = None
        if p['status'] == 'CANCELLED_ADMIN':
            if p.get('admin_cancel_receipt_verified') is not True:
                raise ValueError('CAUCION_ADMIN_RECEIPT_REQUIRED')
            cancelled = aware_datetime(p['admin_cancelled_at'])
        if (p['status'] not in {'OPEN','MATURED','CANCELLED_ADMIN'}
                or (p['status']=='MATURED') != bool(settled)
                or maturity <= opened or (settled and settled < maturity)):
            raise ValueError('Cronología de caución inválida')
        return ('FUTURE' if opened > at else
                'CANCELLED_ADMIN' if cancelled is not None and cancelled <= at else
                'MATURED' if settled and settled <= at else 'OPEN')

    def cash_effect(self, currency, as_of, *, connection=None):
        at = aware_datetime(as_of)
        result = ZERO
        for p in self.positions(currency=currency, connection=connection, as_of=as_of):
            state = self.state_at(p, at)
            if state in {'FUTURE','CANCELLED_ADMIN'}:
                continue
            principal, fees = Decimal(p["principal"]), Decimal(p["total_fees"])
            if state == "MATURED":
                result += Decimal(p["gross_interest"]) - fees
            else:
                result -= principal + (fees if p["fee_payment"] == "UPFRONT" else ZERO)
        return result

    def valuation(self, as_of, currency="ARS", *, connection=None):
        at = aware_datetime(as_of).astimezone(TZ)
        principal = accrued = unrealized = realized = ZERO
        for p in self.positions(currency=currency, connection=connection, as_of=as_of):
            state = self.state_at(p, at)
            if state in {'FUTURE','CANCELLED_ADMIN'}:
                continue
            fees, interest = Decimal(p["total_fees"]), Decimal(p["gross_interest"])
            if state == "MATURED":
                realized += interest - fees
                continue
            amount = Decimal(p["principal"])
            elapsed = max(0, min(p["interest_days"], (at.date() - aware_datetime(p["opened_at"]).astimezone(TZ).date()).days))
            earned = money(amount * Decimal(p["annual_rate_fraction"]) * elapsed / p["day_count_basis"])
            principal += amount
            # Se reconoce el costo comprometido completo; no se presenta
            # como ganancia el interés bruto de una colocación con gastos.
            accrued += earned - (fees if p["fee_payment"] == "MATURITY" else ZERO)
            # Los costos UPFRONT ya redujeron caja al abrir. Restarlos otra
            # vez en el PnL no realizado duplicaba el gasto en el reporte.
            unrealized += earned - (fees if p["fee_payment"] == "MATURITY" else ZERO)
        return {"principal": principal, "accrued": accrued, "unrealized": unrealized, "realized": realized}

    def place(self, offer, principal, request_id, as_of, available_cash, *, reserve=ZERO,
              participation=Decimal("0.10"), max_quote_age_seconds=60, admission=None, connection=None):
        if connection is None:
            with self.store.connect() as c:
                c.execute('BEGIN IMMEDIATE')
                return self.place(offer,principal,request_id,as_of,available_cash,reserve=reserve,
                    participation=participation,max_quote_age_seconds=max_quote_age_seconds,
                    admission=admission,connection=c)
        return self._place_locked(offer,principal,request_id,as_of,available_cash,reserve=reserve,
            participation=participation,max_quote_age_seconds=max_quote_age_seconds,
            admission=admission,connection=connection)

    def _place_locked(self, offer, principal, request_id, as_of, available_cash, *, reserve,
                      participation, max_quote_age_seconds, admission, connection):
        at = aware_datetime(as_of)
        principal = decimal_value(principal, "capital", positive=True)
        reserve = decimal_value(reserve, "reserva de caja", nonnegative=True)
        participation = decimal_value(participation, "participación", positive=True)
        max_quote_age_seconds = decimal_value(max_quote_age_seconds, 'antigüedad de caución', nonnegative=True)
        if participation > 1:
            raise ValueError("Límite de liquidez o frescura inválido")
        if not isinstance(request_id, str) or not request_id.strip():
            raise ValueError("Falta clave idempotente de la colocación")
        interest, fees, net = offer.economics(principal)
        terms = json.dumps(asdict(offer), sort_keys=True, default=str)
        request_fingerprint = hashlib.sha256((terms + "|" + format(principal.normalize(), "f")).encode()).hexdigest()
        c = connection
        previous = c.execute("SELECT * FROM paper_cauciones WHERE request_id=?", (request_id,)).fetchone()
        if previous:
            if previous['status'] == 'CANCELLED_ADMIN':
                proof = c.execute('''SELECT * FROM paper_caucion_admin_cancellations
                    WHERE paper_id=?''', (previous['paper_id'],)).fetchone()
                validate_position(previous, cancel_receipt=proof)
            else:
                validate_position(previous)
            if previous["request_fingerprint"] != request_fingerprint:
                raise ValueError("Clave de colocación reutilizada con términos diferentes")
            return dict(previous)
        # Both explicit placement and allocation converge here, inside the
        # SQLite transaction. A replay above does not open a new PAPER position.
        # The workspace resolver identifies the actual PAPER runtime database,
        # including an explicit path override. Synthetic books remain isolated
        # even when their tests run inside an image with POROTA_BUILD_SHA set.
        from rc6_paper_runtime_safety import runtime_admission_context
        runtime_book, safety_error = runtime_admission_context(self.store, connection=c)
        if safety_error:
            raise ValueError(safety_error)
        if runtime_book:
            from rc6_paper_admission_gate import admission_status
            allowed, reason = admission_status()
            if not allowed:
                raise ValueError(reason or "NO_GO_PAPER_SCALPING")
            market_error = _runtime_caucion_market_error(at)
            if market_error:
                raise ValueError(market_error)
            source_error = _runtime_caucion_book_error(self.store, c, offer, at)
            if source_error:
                raise ValueError(source_error)
        age = (at - aware_datetime(offer.quoted_at)).total_seconds()
        if age < 0 or age > max_quote_age_seconds:
            raise ValueError("Cotización de caución vencida o futura")
        if at.astimezone(TZ).date().isoformat() != offer.start_date or at >= aware_datetime(offer.maturity_at):
            raise ValueError("Inicio o vencimiento incompatible con el reloj de la operación")
        if principal + self.used_principal(offer,connection=c) > automatic_liquidity_cap(offer, participation):
            raise ValueError("Capital supera la participación permitida en la profundidad")
        if net <= 0:
            raise ValueError("La caución no tiene retorno neto positivo con estos costos")
        required = principal + (fees if offer.fee_payment == "UPFRONT" else ZERO)
        # Validate ledger/cash before a risk evaluation that may persist state.
        # A malformed pending receipt must fail without rewriting DailyRisk.
        cash = decimal_value(available_cash(offer.currency, at, c), 'caja disponible')
        if required + reserve > cash:
            raise ValueError("Caja liquidada insuficiente después de reservar fondos")
        if admission:
            error = admission(c,offer.currency,at,fees)
            if error:
                # Existing latch/state is intentionally preserved when the
                # financial gate itself rejects an otherwise valid request.
                c.commit()
                raise ValueError(error)
        paper_id = "PAPER-CAUCION-" + uuid.uuid4().hex
        c.execute("""INSERT INTO paper_cauciones VALUES(
          ?,?,?,'PRODUCTION_PAPER',?,?,'OPEN',?,?,?,?,?,?,?,?,?,NULL,?)""",
          (paper_id, request_id, request_fingerprint, offer.instrument_id, offer.currency,
           str(principal), str(offer.annual_rate_fraction), offer.interest_days,
           offer.day_count_basis, str(interest), str(fees), offer.fee_payment,
           at.isoformat(), offer.maturity_at, terms))
        c.execute("INSERT INTO paper_events VALUES(NULL,?,?,?,?,?)",
                  (at.isoformat(), "PRODUCTION_PAPER", "PAPER_CAUCION_PLACED", paper_id,
                   f"Colocadora simulada {principal} {offer.currency}; vence {offer.maturity_at}; neto estimado {net}"))
        return dict(c.execute("SELECT * FROM paper_cauciones WHERE paper_id=?", (paper_id,)).fetchone())

    def settle_due(self, as_of):
        at = aware_datetime(as_of)
        settled = []
        with self.store.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            rows = self.positions(status='OPEN',connection=c)
            for p in rows:
                if aware_datetime(p["maturity_at"]) > at:
                    continue
                c.execute("UPDATE paper_cauciones SET status='MATURED',settled_at=? WHERE paper_id=? AND status='OPEN'",
                          (at.isoformat(), p["paper_id"]))
                c.execute("INSERT INTO paper_events VALUES(NULL,?,?,?,?,?)",
                          (at.isoformat(), "PRODUCTION_PAPER", "PAPER_CAUCION_MATURED", p["paper_id"],
                           f"Capital e interés neto acreditados en simulación; vencimiento contractual {p['maturity_at']}"))
                settled.append(p["paper_id"])
        return settled
