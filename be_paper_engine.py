"""Motor paper independiente para observar PPI Produccion sin operar cuentas.

Usa exclusivamente Decimal y una base SQLite separada. Cada operacion queda
marcada PRODUCTION_PAPER/SIMULATED y los identificadores comienzan con PAPER-.
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from typing import Optional

from bs_instrument_contracts import InstrumentContract, SPOT_FAMILIES, family_name
from bt_caucion_paper import (CaucionBook, init_schema as init_financial_schema,
                              pending_proceeds, record_sale)


SOURCE = "PRODUCTION_PAPER"
STRATEGY_VERSION = "paper-momentum-v1"
ZERO = Decimal("0")


def D(value, default="0") -> Decimal:
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else Decimal(default)
    except Exception:
        return Decimal(default)


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


@dataclass(frozen=True)
class Quote:
    symbol: str
    asset_class: str
    settlement: str
    last: Decimal
    bid: Decimal
    ask: Decimal
    bid_size: Decimal
    ask_size: Decimal
    observed_at: str
    contract: Optional[InstrumentContract] = None


class PaperStore:
    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.init_db()

    def connect(self):
        conn = sqlite3.connect(self.path, timeout=20)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def init_db(self):
        with self.connect() as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS observer_state(
              id INTEGER PRIMARY KEY CHECK(id=1), mode TEXT NOT NULL,
              process_state TEXT NOT NULL, session_state TEXT NOT NULL,
              ppi_auth TEXT NOT NULL, heartbeat_at TEXT, last_market_data_at TEXT,
              real_orders_sent INTEGER NOT NULL DEFAULT 0,
              http_allowed INTEGER NOT NULL DEFAULT 0,
              http_blocked INTEGER NOT NULL DEFAULT 0,
              detail TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS market_snapshots(
              id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL,
              observed_at TEXT NOT NULL, symbol TEXT NOT NULL, asset_class TEXT NOT NULL,
              settlement TEXT NOT NULL, last TEXT NOT NULL, bid TEXT NOT NULL,
              ask TEXT NOT NULL, bid_size TEXT NOT NULL, ask_size TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS idx_snap_symbol ON market_snapshots(symbol,id);
            CREATE TABLE IF NOT EXISTS paper_decisions(
              id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL,
              strategy_version TEXT NOT NULL, decision_key TEXT NOT NULL UNIQUE,
              decided_at TEXT NOT NULL, symbol TEXT NOT NULL, action TEXT NOT NULL,
              score TEXT NOT NULL, reason TEXT NOT NULL, features_json TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS paper_positions(
              paper_id TEXT PRIMARY KEY, source TEXT NOT NULL, strategy_version TEXT NOT NULL,
              symbol TEXT NOT NULL, asset_class TEXT NOT NULL, settlement TEXT NOT NULL,
              status TEXT NOT NULL, quantity TEXT NOT NULL, entry_price TEXT NOT NULL,
              entry_cost TEXT NOT NULL, stop_price TEXT NOT NULL, target_price TEXT NOT NULL,
              opened_at TEXT NOT NULL, closed_at TEXT, exit_price TEXT, exit_cost TEXT,
              gross_pnl TEXT, net_pnl TEXT, close_reason TEXT, features_json TEXT NOT NULL,
              max_favorable TEXT NOT NULL DEFAULT '0', max_adverse TEXT NOT NULL DEFAULT '0');
            CREATE TABLE IF NOT EXISTS paper_fills(
              id INTEGER PRIMARY KEY AUTOINCREMENT, paper_id TEXT NOT NULL,
              source TEXT NOT NULL, side TEXT NOT NULL, filled_at TEXT NOT NULL,
              quantity TEXT NOT NULL, price TEXT NOT NULL, costs TEXT NOT NULL,
              slippage TEXT NOT NULL, FOREIGN KEY(paper_id) REFERENCES paper_positions(paper_id));
            CREATE TABLE IF NOT EXISTS paper_events(
              id INTEGER PRIMARY KEY AUTOINCREMENT, event_at TEXT NOT NULL,
              source TEXT NOT NULL, event_type TEXT NOT NULL, paper_id TEXT,
              detail TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS paper_learning_samples(
              paper_id TEXT PRIMARY KEY, source TEXT NOT NULL, strategy_version TEXT NOT NULL,
              feature_timestamp TEXT NOT NULL, features_json TEXT NOT NULL,
              label_timestamp TEXT, net_return_pct TEXT, outcome TEXT, duration_minutes INTEGER,
              FOREIGN KEY(paper_id) REFERENCES paper_positions(paper_id));
            CREATE TABLE IF NOT EXISTS paper_equity(
              id INTEGER PRIMARY KEY AUTOINCREMENT, measured_at TEXT NOT NULL,
              source TEXT NOT NULL, cash TEXT NOT NULL, exposure TEXT NOT NULL,
              unrealized_pnl TEXT NOT NULL, realized_pnl TEXT NOT NULL, equity TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS ai_shadow_evaluations(
              id INTEGER PRIMARY KEY AUTOINCREMENT, evaluated_at TEXT NOT NULL,
              symbol TEXT NOT NULL, decision TEXT NOT NULL, score TEXT NOT NULL,
              veto_risk INTEGER NOT NULL, reason TEXT NOT NULL, model TEXT NOT NULL,
              input_json TEXT NOT NULL, raw_json TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS http_audit(
              id INTEGER PRIMARY KEY AUTOINCREMENT, happened_at TEXT NOT NULL,
              method TEXT NOT NULL, path TEXT NOT NULL, result TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS trade_gate_evaluations(
              id INTEGER PRIMARY KEY AUTOINCREMENT, evaluated_at TEXT NOT NULL,
              decision_key TEXT NOT NULL UNIQUE, symbol TEXT NOT NULL,
              technical_gate TEXT NOT NULL, ai_gate TEXT NOT NULL,
              patrimonial_gate TEXT NOT NULL, final_result TEXT NOT NULL,
              reason TEXT NOT NULL, paper_id TEXT, detail_json TEXT NOT NULL);
            INSERT OR IGNORE INTO observer_state(id,mode,process_state,session_state,ppi_auth)
              VALUES(1,'PRODUCTION_PAPER','STOPPED','UNKNOWN','NOT_ATTEMPTED');
            """)
        init_financial_schema(self)

    def audit_http(self, method, path, result):
        with self.connect() as c:
            c.execute("INSERT INTO http_audit VALUES(NULL,?,?,?,?)",
                      (now_iso(), method, path, result))

    def state(self, **updates):
        allowed = {"process_state", "session_state", "ppi_auth", "heartbeat_at",
                   "last_market_data_at", "real_orders_sent", "http_allowed",
                   "http_blocked", "detail"}
        updates = {k: v for k, v in updates.items() if k in allowed}
        if not updates:
            return
        sql = ",".join(f"{k}=?" for k in updates)
        with self.connect() as c:
            c.execute(f"UPDATE observer_state SET {sql} WHERE id=1", tuple(updates.values()))

    def add_quote(self, q: Quote):
        with self.connect() as c:
            c.execute("""INSERT INTO market_snapshots VALUES(NULL,?,?,?,?,?,?,?,?,?,?)""",
                      (SOURCE, q.observed_at, q.symbol, q.asset_class, q.settlement,
                       str(q.last), str(q.bid), str(q.ask), str(q.bid_size), str(q.ask_size)))

    def prices(self, symbol: str, limit=30, *, asset_class=None, settlement=None):
        filters, params = ["symbol=?"], [symbol]
        if asset_class is not None:
            filters.append("asset_class=?")
            params.append(asset_class)
        if settlement is not None:
            filters.append("settlement=?")
            params.append(settlement)
        with self.connect() as c:
            rows = c.execute("SELECT last FROM market_snapshots WHERE " +
                             " AND ".join(filters) + " ORDER BY id DESC LIMIT ?",
                             (*params, limit)).fetchall()
        return [D(r[0]) for r in reversed(rows)]

    def open_position(self, symbol: str):
        with self.connect() as c:
            row = c.execute("SELECT * FROM paper_positions WHERE symbol=? AND status='OPEN'",
                            (symbol,)).fetchone()
        return dict(row) if row else None

    def open_positions(self):
        with self.connect() as c:
            return [dict(r) for r in c.execute(
                "SELECT * FROM paper_positions WHERE status='OPEN' ORDER BY opened_at")]

    def recent_closed(self, limit=50):
        with self.connect() as c:
            return [dict(r) for r in c.execute(
                "SELECT * FROM paper_positions WHERE status='CLOSED' ORDER BY closed_at DESC LIMIT ?",
                (limit,))]

    def record_decision(self, key: str, q: Quote, action: str, score: Decimal,
                        reason: str, features: dict) -> bool:
        try:
            with self.connect() as c:
                c.execute("""INSERT INTO paper_decisions VALUES(NULL,?,?,?,?,?,?,?,?,?)""",
                          (SOURCE, STRATEGY_VERSION, key, q.observed_at, q.symbol,
                           action, str(score), reason, json.dumps(features, ensure_ascii=False)))
            return True
        except sqlite3.IntegrityError:
            return False

    def event(self, kind: str, detail: str, paper_id=None):
        with self.connect() as c:
            c.execute("INSERT INTO paper_events VALUES(NULL,?,?,?,?,?)",
                      (now_iso(), SOURCE, kind, paper_id, detail[:1000]))

    def record_ai(self, q: Quote, result: dict, features: dict):
        with self.connect() as c:
            c.execute("""INSERT INTO ai_shadow_evaluations
              (evaluated_at,symbol,decision,score,veto_risk,reason,model,input_json,raw_json)
              VALUES(?,?,?,?,?,?,?,?,?)""",
              (q.observed_at, q.symbol, str(result.get("decision", "VETO")),
               str(result.get("score", 0)), int(bool(result.get("veto", True))),
               str(result.get("reason", ""))[:800], str(result.get("model", ""))[:120],
               json.dumps(features, ensure_ascii=False, default=str),
               json.dumps(result.get("raw", result), ensure_ascii=False, default=str)[:8000]))

    def record_gates(self, q: Quote, decision_key: str, technical: str,
                     ai: str, patrimonial: str, final: str, reason: str,
                     paper_id=None, detail=None):
        """Conserva la secuencia completa; APPROVE de IA no equivale a fill."""
        with self.connect() as c:
            c.execute("""INSERT OR REPLACE INTO trade_gate_evaluations
              (evaluated_at,decision_key,symbol,technical_gate,ai_gate,
               patrimonial_gate,final_result,reason,paper_id,detail_json)
              VALUES(?,?,?,?,?,?,?,?,?,?)""",
              (q.observed_at, decision_key, q.symbol, technical, ai,
               patrimonial, final, str(reason)[:1000], paper_id,
               json.dumps(detail or {}, ensure_ascii=False, default=str)[:8000]))


class PaperBroker:
    def __init__(self, store: PaperStore, initial_cash="1000000",
                 risk_pct="0.005", max_positions=3, fee_rate="0.00605",
                 slippage_bps="2", participation="0.10",
                 max_position_pct="0.25", max_total_exposure_pct="0.60",
                 ai_gate=None, require_ai=False, context_fn=None,
                 initial_cash_usd="0"):
        self.store = store
        self.initial_cash = D(initial_cash)
        self.risk_pct = D(risk_pct)
        self.max_positions = int(max_positions)
        self.fee_rate = D(fee_rate)
        self.slippage = D(slippage_bps) / D(10000)
        self.participation = D(participation)
        self.max_position_pct = D(max_position_pct)
        self.max_total_exposure_pct = D(max_total_exposure_pct)
        self.ai_gate = ai_gate
        self.require_ai = bool(require_ai)
        self.context_fn = context_fn
        self.initial_cash_usd = D(initial_cash_usd)
        self.cauciones = CaucionBook(store)

    def _cost(self, price, qty, asset_class="ACCIONES"):
        """Costo de una punta; el spread ya vive en bid/ask y no se duplica."""
        import au_fee_schedule
        family = family_name(asset_class)
        if family not in SPOT_FAMILIES:
            raise ValueError("La familia requiere un cálculo de costos específico")
        rate = D(au_fee_schedule.costo_por_tramo(family), "-1")
        if rate < 0:
            raise ValueError("tarifario no valido para el costo por tramo")
        return (D(price) * D(qty) * rate).quantize(Decimal("0.01"))

    @staticmethod
    def _quantity_in_budget(unit_amount, budget, extra_cost):
        """Mayor cantidad entera que cabe incluyendo costos redondeados."""
        if unit_amount <= 0 or budget <= 0:
            return ZERO
        low, high = 0, int(budget / unit_amount)
        while low < high:
            middle = (low + high + 1) // 2
            if unit_amount * middle + extra_cost(D(middle)) <= budget:
                low = middle
            else:
                high = middle - 1
        return D(low)

    @staticmethod
    def _position_multiplier(position):
        # Las posiciones antiguas conservan sus unidades originales. Cambiar
        # retrospectivamente su factor alteraría fills y resultados históricos.
        features = json.loads(position.get("features_json") or "{}")
        factor = D(features.get("contract_cash_multiplier", "1"), "-1")
        if factor <= 0:
            raise ValueError("Posición con factor monetario inválido")
        return factor

    def _cash(self, as_of=None, currency="ARS"):
        """Caja disponible por moneda; no incluye ventas aún sin liquidar."""
        if currency == "USD":
            return self.initial_cash_usd + self.cauciones.cash_effect("USD")
        if currency != "ARS":
            raise ValueError("Moneda sin libro de caja")
        closed = self.store.recent_closed(100000)
        realized = sum((D(r.get("net_pnl")) for r in closed), ZERO)
        committed = sum((D(p["entry_price"]) * D(p["quantity"]) * self._position_multiplier(p) + D(p["entry_cost"])
                         for p in self.store.open_positions()), ZERO)
        return (self.initial_cash + realized - committed + self.cauciones.cash_effect("ARS")
                - pending_proceeds(self.store, as_of or now_iso(), "ARS"))

    def place_caucion(self, offer, principal, request_id, as_of=None, *, reserve="0"):
        """Colocación PAPER explícita; no coloca órdenes reales ni elige plazo."""
        return self.cauciones.place(
            offer, principal, request_id, as_of or now_iso(),
            lambda currency, at: self._cash(as_of=at, currency=currency),
            reserve=D(reserve, "-1"), participation=self.participation)

    def settle_cauciones(self, as_of=None):
        return self.cauciones.settle_due(as_of or now_iso())

    def threshold(self):
        closed = self.store.recent_closed(20)
        if len(closed) < 5:
            return D("0.62")
        wins = sum(1 for p in closed if D(p.get("net_pnl")) > 0)
        rate = D(wins) / D(len(closed))
        return D("0.67") if rate < D("0.45") else D("0.58") if rate > D("0.65") else D("0.62")

    def decide(self, q: Quote):
        if D(q.bid) <= 0 or D(q.ask) < D(q.bid) or D(q.ask_size) <= 0:
            return "HOLD", ZERO, "Puntas o profundidad invalidas", {"samples": 0}
        values = self.store.prices(q.symbol, 20, asset_class=q.asset_class,
                                   settlement=q.settlement)
        if len(values) < 8:
            return "HOLD", D("0"), f"Aprendiendo serie: {len(values)}/8 muestras", {"samples": len(values)}
        short = sum(values[-3:], ZERO) / D(3)
        long = sum(values[-8:], ZERO) / D(8)
        momentum = (short / long - 1) if long else ZERO
        spread = (q.ask / q.bid - 1) if q.bid else D("99")
        score = max(ZERO, min(D(1), D("0.5") + momentum * D(40) - spread * D(10)))
        features = {"sma3": str(short), "sma8": str(long), "momentum": str(momentum),
                    "spread": str(spread), "samples": len(values),
                    "paper_threshold": str(self.threshold())}
        if D(q.bid) <= 0 or D(q.ask) < D(q.bid) or D(q.ask_size) <= 0:
            return "HOLD", score, "Puntas o profundidad insuficientes", features
        if spread > D("0.02"):
            return "HOLD", score, "Spread superior al 2%", features
        if score < self.threshold():
            return "HOLD", score, "Score paper debajo del umbral adaptativo", features
        return "BUY", score, "Momentum positivo y friccion admisible", features

    def on_quote(self, q: Quote):
        if self._maybe_close(q):
            return
        if self.store.open_position(q.symbol):
            return
        action, score, reason, features = self.decide(q)
        bucket = q.observed_at[:16]
        key = f"{STRATEGY_VERSION}:{q.symbol}:{bucket}:{action}"
        if not self.store.record_decision(key, q, action, score, reason, features):
            return
        self.store.event("DECISION_PAPER", f"{q.symbol} {action}: {reason}")
        if action == "BUY":
            if self.ai_gate is None and self.require_ai:
                self.store.event("AI_VETO_PAPER", f"{q.symbol}: Gemini no disponible; porton cerrado")
                self.store.record_gates(q, key, "APPROVE", "VETO", "NOT_EVALUATED",
                                        "BLOCKED", "Gemini no disponible; portón cerrado",
                                        detail=features)
                return
            if self.ai_gate is not None:
                try:
                    context = self.context_fn(q.symbol) if self.context_fn else {}
                    ai = self.ai_gate.evaluate(q, score, features, context)
                except Exception as exc:
                    ai = {"decision": "VETO", "score": 0, "veto": True,
                          "reason": f"Gemini no disponible: {type(exc).__name__}: {str(exc)[:300]}",
                          "model": "NO_DISPONIBLE", "raw": {}}
                self.store.record_ai(q, ai, features)
                features["gemini"] = {key: ai.get(key) for key in
                                       ("decision", "score", "veto", "reason", "model")}
                if ai.get("decision") != "APPROVE" or ai.get("veto", True):
                    self.store.event("AI_VETO_PAPER", f"{q.symbol}: {ai.get('reason')}")
                    self.store.record_gates(q, key, "APPROVE", str(ai.get("decision", "VETO")),
                                            "NOT_EVALUATED", "BLOCKED",
                                            str(ai.get("reason") or "Gemini vetó la apertura"),
                                            detail=features)
                    return
            opened, gate_reason, paper_id = self._open(q, score, features)
            self.store.record_gates(
                q, key, "APPROVE", "APPROVE",
                "APPROVE" if opened else "BLOCKED",
                "OPENED_SIMULATED" if opened else "BLOCKED", gate_reason,
                paper_id=paper_id, detail=features)

    def _open(self, q: Quote, score: Decimal, features: dict):
        try:
            family = family_name(q.asset_class)
        except ValueError as exc:
            return False, str(exc), None
        if family not in SPOT_FAMILIES:
            return False, f"{family} requiere su ciclo financiero específico; no se compra como una acción", None
        factor, step = D(1), D(1)
        if q.contract is not None:
            spec = q.contract
            if ((spec.symbol, spec.family, spec.settlement) != (q.symbol, family, q.settlement)
                    or spec.currency != "ARS" or spec.market != "BYMA"):
                return False, "Contrato incompatible con esta cotización y libro de caja ARS/BYMA", None
            factor, step = spec.cash_multiplier, spec.quantity_step
            if step < 1 or step != step.to_integral_value():
                return False, "Este ejecutor de contado requiere cantidades enteras", None
        elif family in {"BONOS", "LETRAS", "OBLIGACIONES"}:
            return False, "Falta confirmar factor de precio/VN y lote del instrumento de renta fija", None
        if D(q.bid) <= 0 or D(q.ask) < D(q.bid) or D(q.ask_size) <= 0:
            return False, "Puntas o profundidad invalidas para una compra simulada", None
        if self.store.open_position(q.symbol):
            return False, "Ya existe una posicion abierta del simbolo", None
        if len(self.store.open_positions()) >= self.max_positions:
            self.store.event("REJECTED_PAPER", f"{q.symbol}: maximo de posiciones paper")
            return False, "Límite máximo de posiciones paper alcanzado", None
        entry = (q.ask * (1 + self.slippage)).quantize(Decimal("0.0001"))
        if entry <= 0:
            return False, "Precio de entrada no representable", None
        stop = entry * D("0.98")
        target = entry * D("1.035")
        modeled_stop_fill = (stop * (1 - self.slippage)).quantize(Decimal("0.0001"))
        # Incluye ambos tramos y deslizamiento de salida, sin afirmar que un
        # stop garantice este precio ante gaps o falta de liquidez.
        by_risk = self._quantity_in_budget(
            (entry - modeled_stop_fill) * factor, self.initial_cash * self.risk_pct,
            lambda qty: self._cost(entry * factor, qty, q.asset_class) +
                        self._cost(modeled_stop_fill * factor, qty, q.asset_class))
        by_cash = self._quantity_in_budget(
            entry * factor, max(ZERO, self._cash(as_of=q.observed_at)),
            lambda qty: self._cost(entry * factor, qty, q.asset_class))
        by_book = (q.ask_size * self.participation).to_integral_value(ROUND_DOWN)
        by_position_cap = (self.initial_cash * self.max_position_pct / (entry * factor)).to_integral_value(ROUND_DOWN)
        current_exposure = sum((D(p["entry_price"]) * D(p["quantity"]) * self._position_multiplier(p)
                                for p in self.store.open_positions()), ZERO)
        exposure_remaining = max(
            ZERO, self.initial_cash * self.max_total_exposure_pct - current_exposure
        )
        by_total_cap = (exposure_remaining / (entry * factor)).to_integral_value(ROUND_DOWN)
        qty = (min(by_risk, by_cash, by_book, by_position_cap, by_total_cap) / step).to_integral_value(ROUND_DOWN) * step
        features.update({
            "contract_cash_multiplier": str(factor), "contract_quantity_step": str(step),
            "financial_contract": asdict(q.contract) if q.contract else None,
            "initial_capital_ars": str(self.initial_cash),
            "risk_budget_ars": str(self.initial_cash * self.risk_pct),
            "max_position_pct": str(self.max_position_pct),
            "max_total_exposure_pct": str(self.max_total_exposure_pct),
            "qty_by_risk": str(by_risk),
            "qty_by_cash": str(by_cash),
            "qty_by_liquidity": str(by_book),
            "qty_by_position_cap": str(by_position_cap),
            "qty_by_total_cap": str(by_total_cap),
        })
        if qty < 1:
            self.store.event("REJECTED_PAPER", f"{q.symbol}: capital/liquidez insuficiente")
            limits = {
                "riesgo": by_risk, "caja": by_cash, "profundidad": by_book,
                "tope_posicion": by_position_cap, "exposicion_total": by_total_cap,
            }
            binding = min(limits, key=lambda name: limits[name])
            return False, (f"Portón patrimonial/liquidez: {binding} dejó cantidad "
                           f"ejecutable en {limits[binding]}"), None
        paper_id = "PAPER-" + uuid.uuid4().hex
        cost = self._cost(entry * factor, qty, q.asset_class)
        with self.store.connect() as c:
            # Serializa contra otras compras y colocaciones de caución.
            # No se hace red ni IA dentro de esta transacción.
            c.execute("BEGIN IMMEDIATE")
            opened = self.store.open_positions()
            exposure_now = sum((D(p["entry_price"]) * D(p["quantity"]) * self._position_multiplier(p) for p in opened), ZERO)
            if (len(opened) >= self.max_positions or any(p["symbol"] == q.symbol for p in opened)
                    or entry * qty * factor + cost > self._cash(as_of=q.observed_at)
                    or exposure_now + entry * qty * factor > self.initial_cash * self.max_total_exposure_pct):
                return False, "Caja, posiciones o exposición cambiaron antes de registrar la compra", None
            c.execute("""INSERT INTO paper_positions
              (paper_id,source,strategy_version,symbol,asset_class,settlement,status,
               quantity,entry_price,entry_cost,stop_price,target_price,opened_at,features_json)
              VALUES(?,?,?,?,?,?,'OPEN',?,?,?,?,?,?,?)""",
              (paper_id, SOURCE, STRATEGY_VERSION, q.symbol, q.asset_class, q.settlement,
               str(qty), str(entry), str(cost), str(stop), str(target), q.observed_at,
               json.dumps(features, ensure_ascii=False, default=str)))
            c.execute("INSERT INTO paper_fills VALUES(NULL,?,?,?,?,?,?,?,?)",
                      (paper_id, SOURCE, "BUY_SIMULATED", q.observed_at, str(qty),
                       str(entry), str(cost), str(entry-q.ask)))
            c.execute("INSERT INTO paper_learning_samples VALUES(?,?,?,?,?,?,?,?,?)",
                      (paper_id, SOURCE, STRATEGY_VERSION, q.observed_at,
                       json.dumps(features, ensure_ascii=False, default=str), None, None, None, None))
            c.execute("INSERT INTO paper_events VALUES(NULL,?,?,?,?,?)",
                      (q.observed_at, SOURCE, "PAPER_FILLED_BUY", paper_id,
                       f"Compra simulada {qty} {q.symbol} @ {entry}"))
        return True, "Todos los portones aprobaron; compra simulada registrada", paper_id

    def _maybe_close(self, q: Quote):
        p = self.store.open_position(q.symbol)
        if (not p or (p["asset_class"], p["settlement"]) !=
                (q.asset_class, q.settlement) or D(q.bid) <= 0 or D(q.bid_size) <= 0):
            return False
        reason = None
        if q.bid <= D(p["stop_price"]):
            reason = "STOP_PAPER"
        elif q.bid >= D(p["target_price"]):
            reason = "TAKE_PROFIT_PAPER"
        else:
            try:
                opened = datetime.fromisoformat(p["opened_at"])
                age = (datetime.fromisoformat(q.observed_at) - opened).total_seconds() / 60
                if age >= int(os.getenv("PAPER_MAX_HOLD_MINUTES", "180")):
                    reason = "MAX_HOLD_PAPER"
            except Exception:
                pass
        if reason:
            return self._close(p, q, reason)
        return False

    def _close(self, p: dict, q: Quote, reason: str):
        """Cierre total simulado: sin inventar volumen ni duplicar un fill.

        Las salidas parciales requieren un libro propio y quedan pendientes.
        Por ahora se rechaza un cierre que excede la participacion disponible.
        La futura integracion del supervisor debe validar frescura y sesion
        usando su reloj; este metodo solo valida secuencia temporal y fill.
        """
        if (p["symbol"], p["asset_class"], p["settlement"]) != (
                q.symbol, q.asset_class, q.settlement):
            return False
        qty = D(p["quantity"])
        factor = self._position_multiplier(p)
        if q.contract and (q.contract.cash_multiplier != factor or
                           q.contract.symbol != q.symbol or q.contract.settlement != q.settlement or
                           q.contract.family != family_name(q.asset_class) or q.contract.currency != "ARS"):
            return False
        if D(q.bid) <= 0 or qty <= 0 or qty > (D(q.bid_size) * self.participation):
            self.store.event("EXIT_PENDING_NO_LIQUIDITY",
                             "Profundidad insuficiente para cerrar toda la posicion", p["paper_id"])
            return False
        try:
            opened = datetime.fromisoformat(p["opened_at"].replace("Z", "+00:00"))
            observed = datetime.fromisoformat(q.observed_at.replace("Z", "+00:00"))
            if opened.tzinfo is None or observed.tzinfo is None or observed < opened:
                return False
            duration = int((observed-opened).total_seconds() / 60)
        except (ValueError, TypeError):
            return False
        exit_price = (q.bid * (1 - self.slippage)).quantize(Decimal("0.0001"))
        exit_cost = self._cost(exit_price * factor, qty, p.get("asset_class", "ACCIONES"))
        gross = (exit_price - D(p["entry_price"])) * qty * factor
        net = gross - D(p["entry_cost"]) - exit_cost
        invested = D(p["entry_price"]) * qty * factor
        ret = (net / invested * 100) if invested else ZERO
        outcome = "WIN" if net > 0 else "LOSS" if net < 0 else "FLAT"
        with self.store.connect() as c:
            updated = c.execute("""UPDATE paper_positions SET status='CLOSED',closed_at=?,exit_price=?,
                         exit_cost=?,gross_pnl=?,net_pnl=?,close_reason=?
                         WHERE paper_id=? AND status='OPEN' AND quantity=?
                         AND entry_price=? AND entry_cost=?""",
                      (q.observed_at, str(exit_price), str(exit_cost), str(gross), str(net),
                       reason, p["paper_id"], p["quantity"], p["entry_price"], p["entry_cost"]))
            if updated.rowcount != 1:
                return False
            record_sale(c, p["paper_id"], p["settlement"], q.observed_at,
                        exit_price * qty * factor - exit_cost)
            c.execute("INSERT INTO paper_fills VALUES(NULL,?,?,?,?,?,?,?,?)",
                      (p["paper_id"], SOURCE, "SELL_SIMULATED", q.observed_at, str(qty),
                       str(exit_price), str(exit_cost), str(q.bid-exit_price)))
            c.execute("""UPDATE paper_learning_samples SET label_timestamp=?,net_return_pct=?,
                         outcome=?,duration_minutes=? WHERE paper_id=?""",
                      (q.observed_at, str(ret), outcome, duration, p["paper_id"]))
            c.execute("INSERT INTO paper_events VALUES(NULL,?,?,?,?,?)",
                      (q.observed_at, SOURCE, "PAPER_FILLED_SELL", p["paper_id"],
                       f"Venta simulada {qty} {q.symbol} @ {exit_price}; PnL neto {net}"))
        return True

    def mark_equity(self, quotes: dict, as_of=None):
        measured_at = as_of or now_iso()
        exposure = unrealized = ZERO
        for p in self.store.open_positions():
            q = quotes.get((p["symbol"], p["asset_class"], p["settlement"])) or quotes.get(p["symbol"])
            if q and (q.symbol, q.asset_class, q.settlement) != (p["symbol"], p["asset_class"], p["settlement"]):
                q = None
            mark = q.bid if q else D(p["entry_price"])
            qty = D(p["quantity"]) * self._position_multiplier(p)
            exposure += mark * qty
            unrealized += (mark - D(p["entry_price"])) * qty - D(p["entry_cost"])
        realized = sum((D(p.get("net_pnl")) for p in self.store.recent_closed(100000)), ZERO)
        caucion = self.cauciones.valuation(measured_at)
        unrealized += caucion["unrealized"]
        realized += caucion["realized"]
        cash = self._cash(as_of=measured_at)
        # Un crédito sin liquidar y una caución siguen siendo patrimonio,
        # pero no se muestran como efectivo que se pueda volver a gastar.
        equity = (cash + exposure + pending_proceeds(self.store, measured_at)
                  + caucion["principal"] + caucion["accrued"])
        with self.store.connect() as c:
            c.execute("INSERT INTO paper_equity VALUES(NULL,?,?,?,?,?,?,?)",
                      (measured_at, SOURCE, str(cash), str(exposure), str(unrealized),
                       str(realized), str(equity)))
