"""Cauciones colocadoras y disponibilidad de caja del simulador v17.

Sin acceso al broker. El capital se inmoviliza y sólo vuelve al vencimiento
contractual. No hay stop, venta de la caución ni reinversión automática.
Los términos normalizados deben estar respaldados por datos del instrumento.
"""

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

from bs_instrument_contracts import aware_datetime, cash_currency, decimal_value
from bl_candle_engine import fingerprint, stamp
import cd_spot_ledger as spot_ledger

ZERO = Decimal("0")
CENT = Decimal("0.01")
TZ = ZoneInfo("America/Argentina/Buenos_Aires")


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


def modeled_sale_settlement(settlement, traded_at):
    """Disponibilidad conservadora PAPER, no un horario oficial del broker.

    CI: mismo instante. T+1: fin del siguiente día del calendario auditado.
    Sin calendario o plazo conocido: pendiente de conciliación, nunca caja.
    No se usa una regla de lunes a viernes si faltan feriados.
    """
    at = aware_datetime(traded_at).astimezone(TZ)
    key = settlement.upper().strip()
    if key in {"INMEDIATA", "CI", "T+0"}:
        return at.isoformat()
    if key not in {"A-24HS", "24HS", "T+1"}:
        return None
    import ak_byma_calendar as calendar
    candidate = at.date()
    for _ in range(370):
        candidate += timedelta(days=1)
        if candidate.year not in calendar.ANIOS_AUDITADOS:
            return None
        if calendar.es_dia_habil_operativo(candidate):
            return datetime.combine(candidate, time.max, TZ).isoformat()
    return None


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
        CREATE TABLE IF NOT EXISTS paper_equity_by_currency(
          id INTEGER PRIMARY KEY AUTOINCREMENT, measured_at TEXT NOT NULL,
          currency TEXT NOT NULL, cash TEXT NOT NULL, exposure TEXT NOT NULL,
          pending_proceeds TEXT NOT NULL, caucion_principal TEXT NOT NULL,
          caucion_accrued TEXT NOT NULL, unrealized_pnl TEXT NOT NULL,
          realized_pnl TEXT NOT NULL, equity TEXT NOT NULL);
        """)
        # Migración aditiva. Cada recibo se crea una sola vez, sin reescribir
        # una fecha que luego haya sido conciliada contra una fuente mejor.
        closed = c.execute("""SELECT p.* FROM paper_positions p
          LEFT JOIN paper_sale_receivables r USING(paper_id)
          WHERE p.status='CLOSED' AND r.paper_id IS NULL""").fetchall()
        for p in closed:
            if spot_ledger.sales(c,p['paper_id']):
                continue
            features = json.loads(p["features_json"] or "{}")
            factor = decimal_value(features.get("contract_cash_multiplier", "1"), "factor histórico", positive=True)
            record_sale(c, p["paper_id"], p["settlement"], p["closed_at"],
                        Decimal(p["exit_price"]) * Decimal(p["quantity"]) * factor - Decimal(p["exit_cost"]),
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
    rows = connection.execute("""SELECT r.*,p.closed_at,p.paper_id AS position_id FROM paper_positions p
        LEFT JOIN paper_sale_receivables r USING(paper_id) WHERE p.currency=? AND p.status='CLOSED'""",
        (cash_currency(currency),)).fetchall()
    pending = spot_ledger.pending(connection,at,currency)
    for row in rows:
        if spot_ledger.sales(connection,row['position_id']):
            continue
        if aware_datetime(row['closed_at']) > at:
            continue
        if row['paper_id'] is None or row['currency'] != currency:
            raise ValueError('Venta sin recibo de liquidación en su moneda')
        amount = decimal_value(row['net_proceeds'], 'producido de venta')
        if amount > 0 and (row['available_at'] is None or aware_datetime(row['available_at']) > at):
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


def validate_position(p):
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
        if (opened.astimezone(TZ).date().isoformat()!=offer.start_date
                or aware_datetime(offer.quoted_at)>opened or maturity!=aware_datetime(offer.maturity_at)
                or maturity<=opened or p['status'] not in {'OPEN','MATURED'}
                or (p['status']=='MATURED')!=(settled is not None)
                or (settled is not None and settled<maturity)):
            raise ValueError('Cronología de caución inválida')
        return offer
    except (ValueError,TypeError,KeyError,AttributeError,ArithmeticError) as exc:
        raise ValueError('CAUCION_LEDGER_INVALID: '+str(exc)) from exc


class CaucionBook:
    def __init__(self, store):
        self.store = store

    def positions(self, *, currency=None, status=None, connection=None):
        if connection is None:
            with self.store.connect() as c:
                return self.positions(currency=currency, status=status, connection=c)
        rows = [dict(r) for r in connection.execute('SELECT * FROM paper_cauciones')]
        # Validar ANTES de filtrar: mover una fila rota de ARS a otra moneda
        # no puede hacer desaparecer su débito de la caja ARS.
        for p in rows:
            validate_position(p)
        return [p for p in rows if (currency is None or p['currency']==currency)
                and (status is None or p['status']==status)]

    def used_principal(self, offer, *, connection):
        """La misma fotografía de liquidez no se repone por otro request_id."""
        used = ZERO
        for p in connection.execute('SELECT * FROM paper_cauciones WHERE instrument_id=? AND currency=?',
                                    (offer.instrument_id,offer.currency)):
            previous = validate_position(p)
            if aware_datetime(previous.quoted_at) > aware_datetime(offer.quoted_at):
                raise ValueError('CAUCION_OLDER_BOOK')
            if book_key(previous) == book_key(offer):
                if book_payload(previous) != book_payload(offer):
                    raise ValueError('CAUCION_CONFLICTING_BOOK')
                used += decimal_value(p['principal'],'capital consumido',positive=True)
        return used

    @staticmethod
    def state_at(p, at):
        opened, maturity = map(aware_datetime, (p['opened_at'], p['maturity_at']))
        settled = aware_datetime(p['settled_at']) if p['settled_at'] else None
        if (p['status'] not in {'OPEN','MATURED'} or (p['status']=='MATURED') != bool(settled)
                or maturity <= opened or (settled and settled < maturity)):
            raise ValueError('Cronología de caución inválida')
        return 'FUTURE' if opened > at else 'MATURED' if settled and settled <= at else 'OPEN'

    def cash_effect(self, currency, as_of, *, connection=None):
        at = aware_datetime(as_of)
        result = ZERO
        for p in self.positions(currency=currency, connection=connection):
            state = self.state_at(p, at)
            if state == 'FUTURE':
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
        for p in self.positions(currency=currency, connection=connection):
            state = self.state_at(p, at)
            if state == 'FUTURE':
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
            unrealized += earned - fees
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
            validate_position(previous)
            if previous["request_fingerprint"] != request_fingerprint:
                raise ValueError("Clave de colocación reutilizada con términos diferentes")
            return dict(previous)
        if admission:
            error = admission(c,offer.currency,at,fees)
            if error:
                # Sólo se evaluó riesgo, todavía no existe colocación.
                # Conservar el latch aunque la petición sea rechazada.
                c.commit()
                raise ValueError(error)
        age = (at - aware_datetime(offer.quoted_at)).total_seconds()
        if age < 0 or age > max_quote_age_seconds:
            raise ValueError("Cotización de caución vencida o futura")
        if at.astimezone(TZ).date().isoformat() != offer.start_date or at >= aware_datetime(offer.maturity_at):
            raise ValueError("Inicio o vencimiento incompatible con el reloj de la operación")
        if principal + self.used_principal(offer,connection=c) > offer.available_principal * participation:
            raise ValueError("Capital supera la participación permitida en la profundidad")
        if net <= 0:
            raise ValueError("La caución no tiene retorno neto positivo con estos costos")
        required = principal + (fees if offer.fee_payment == "UPFRONT" else ZERO)
        cash = decimal_value(available_cash(offer.currency, at, c), 'caja disponible')
        if required + reserve > cash:
            raise ValueError("Caja liquidada insuficiente después de reservar fondos")
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
