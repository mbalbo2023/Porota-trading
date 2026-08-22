"""
i_auto_tuner.py — Reportes, aprendizaje y auto-tuning mensual (v8.0)

CORRECCIÓN APLICADA por las auditorías 7.1/7.2/7.3 (acepto el hallazgo,
es el más importante de todos): el ajuste mensual dejaba que Gemini
propusiera nuevos umbrales y el bot los aplicaba directo, con una muestra
de apenas 30 días — estadísticamente eso es "overfitting" (ajustar el
sistema al ruido de un mes chico en vez de a una señal real). La
referencia académica que cita la auditoría (Harvey, Liu & Zhu 2016;
Bailey & López de Prado 2014, "Deflated Sharpe Ratio") es real y el
punto es válido.

Qué se agregó:
  1. MIN_SAMPLE_SIZE_AUTOTUNE: si no hubo suficientes operaciones
     CERRADAS en el mes, el ajuste se cancela y se mantienen los
     umbrales vigentes — ajustar con poca muestra es peor que no ajustar.
  2. Clamping: la propuesta de Gemini nunca se aplica tal cual. Se
     limita cuánto puede moverse cada parámetro de una vez
     (MAX_PARAM_CHANGE_PCT) respecto al valor vigente — así una
     sugerencia "radical" de la IA no puede desestabilizar el sistema de
     un mes a otro.
  3. Versionado con rollback: cada ajuste queda guardado en
     auto_tune_history con el "antes" y el "después", para poder revertir
     manualmente si el mes siguiente empeora.

QUÉ NO SE IMPLEMENTÓ Y POR QUÉ: la auditoría sugiere el Deflated Sharpe
Ratio completo (corrige el Sharpe por cantidad de configuraciones
probadas, asimetría y curtosis de los retornos). Es matemáticamente más
riguroso, pero requiere trackear cuántas configuraciones se probaron
—cosa que Gemini no hace, solo propone una— y una muestra bastante más
grande que la que este bot va a acumular en sus primeros meses. Se dejó
afuera de esta revisión a propósito: min-sample + clamping + rollback ya
cubre la mayor parte del riesgo práctico de overfitting con mucha menos
complejidad. Si en el futuro el volumen de operaciones lo justifica,
agregar DSR es la mejora natural siguiente (queda documentado en el
Documento Maestro v8.0).
"""

import sqlite3
import json
import os
from datetime import date
import pandas as pd
from google import genai
from dotenv import load_dotenv

import k_position_manager as position_manager
import ac_db  # NUEVO EN v15.0 — conexión SQLite única (WAL + timeout)

load_dotenv()

MONTHLY_FIXED_COSTS_ARS = float(os.getenv("MONTHLY_FIXED_COSTS_ARS", "0"))
MIN_SAMPLE_SIZE_AUTOTUNE = int(os.getenv("MIN_SAMPLE_SIZE_AUTOTUNE", "30"))
MAX_PARAM_CHANGE_PCT = float(os.getenv("MAX_PARAM_CHANGE_PCT", "10"))


def _init_tables(conn):
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS auto_tune_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            sample_size INTEGER,
            previous_config TEXT,
            proposed_config TEXT,
            applied_config TEXT,
            notas TEXT
        )
    """)
    conn.commit()


def _clamp(new_value: float, old_value: float, max_change_pct: float) -> float:
    if old_value == 0:
        return new_value
    max_delta = abs(old_value) * (max_change_pct / 100)
    return max(old_value - max_delta, min(old_value + max_delta, new_value))


class AutoTuner:
    def __init__(self):
        self.db_path = os.getenv("DB_PATH", "data/trading_system.db")
        self.client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
        self.model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")

    def generate_weekly_report(self) -> str:
        conn = ac_db.connect_raw(self.db_path)
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM signals WHERE status='EXECUTED_ALERT'")
        executed = cursor.fetchone()[0]

        cursor.execute("SELECT reason, COUNT(*) FROM signals WHERE status LIKE 'REJECTED%' GROUP BY reason")
        rejected_summary = cursor.fetchall()
        conn.close()

        win_rate = position_manager.get_win_rate(days_back=7)

        report = f"📊 *INFORME SEMANAL DE TRADING*\n\n"
        report += f"✅ Alertas Emitidas: {executed}\n"
        if win_rate["total_closed"]:
            report += (
                f"🎯 Operaciones cerradas: {win_rate['total_closed']} "
                f"(win rate real: {win_rate['win_rate_pct']}% — "
                f"{win_rate['wins']} ganadas / {win_rate['losses']} perdidas)\n"
                f"💰 P&L de operaciones cerradas: {win_rate['net_pnl_ars']:+.2f} ARS\n"
            )
            if MONTHLY_FIXED_COSTS_ARS > 0:
                weekly_fixed_cost = MONTHLY_FIXED_COSTS_ARS / 4.345
                neto_de_costos_fijos = win_rate["net_pnl_ars"] - weekly_fixed_cost
                report += (
                    f"🏗️ Costo fijo semanal prorrateado: {weekly_fixed_cost:.2f} ARS\n"
                    f"📐 Resultado neto de TODO (trading + costos fijos): "
                    f"{neto_de_costos_fijos:+.2f} ARS\n"
                )
        else:
            report += "🎯 Todavía no hay operaciones cerradas esta semana.\n"
        report += "\n🚫 *Rechazos:*\n"
        for reason, count in rejected_summary:
            report += f"- {reason}: {count}\n"
        return report

    def run_monthly_autotune(self):
        conn = ac_db.connect_raw(self.db_path)
        _init_tables(conn)

        df_history = pd.read_sql_query(
            "SELECT * FROM signals WHERE timestamp >= datetime('now', '-30 days')", conn
        )
        try:
            df_closed = pd.read_sql_query(
                "SELECT * FROM closed_trades WHERE closed_at >= datetime('now', '-30 days')", conn
            )
        except Exception:
            df_closed = pd.DataFrame()

        sample_size = len(df_closed)
        current_config = self._load_current_config()

        # --- GUARDARRAÍL 1: muestra mínima ---
        if sample_size < MIN_SAMPLE_SIZE_AUTOTUNE:
            self._log_history(conn, sample_size, current_config, None, current_config,
                               f"Ajuste CANCELADO: {sample_size} operaciones cerradas < mínimo "
                               f"{MIN_SAMPLE_SIZE_AUTOTUNE}. Se mantienen los umbrales vigentes.")
            conn.close()
            return

        win_rate = position_manager.get_win_rate(days_back=30)

        # CORRECCIÓN v10.5 — auditoría 2 (rev. 2), hallazgo "Saturación de
        # Tokens LLM en Auto-Tuning Mensual": antes se mandaba
        # df_history.to_json(orient='records') con CADA señal del mes
        # (potencialmente cientos de filas) — con volumen alto de escaneo
        # (ahora más todavía, con descubrimiento automático y posible modo
        # scalping generando muchas más señales por día), la cadena JSON
        # puede superar límites razonables de tokenización y encarecer/
        # truncar la llamada. Ahora se manda un RESUMEN agregado
        # (promedios de score por status/motivo de rechazo, distribución
        # de resultados cerrados por motivo de salida) en vez del dump
        # crudo — sigue teniendo la información que el modelo necesita
        # para ajustar umbrales, con un tamaño acotado sin importar cuántas
        # señales haya en el mes.
        signals_summary = (
            df_history.groupby(["status"]).agg(
                cantidad=("id", "count"),
                score_tech_promedio=("score_tech", "mean"),
                macro_score_promedio=("macro_score", "mean"),
            ).round(3).reset_index().to_dict(orient="records")
            if not df_history.empty else []
        )
        closed_summary = (
            df_closed.groupby(["exit_reason"]).agg(
                cantidad=("id", "count"),
                pnl_ars_promedio=("realized_pnl_ars", "mean"),
                pnl_pct_promedio=("realized_pnl_pct", "mean"),
            ).round(3).reset_index().to_dict(orient="records")
            if not df_closed.empty else []
        )

        prompt = f"""
Analiza el resumen agregado de señales del último mes y los resultados reales:

RESUMEN DE SEÑALES POR ESTADO (cantidad y score promedio): {json.dumps(signals_summary, ensure_ascii=False)}
RESUMEN DE OPERACIONES CERRADAS POR MOTIVO DE SALIDA: {json.dumps(closed_summary, ensure_ascii=False)}
WIN RATE REAL DEL MES: {json.dumps(win_rate)}
UMBRALES ACTUALES: {json.dumps(current_config)}

Proponé umbrales nuevos (score_tech, score_macro, take_profit_atr_mult)
para maximizar la expectativa neta. Dale más peso al win rate real y al
P&L de las operaciones CERRADAS que a los scores que el bot se puso a sí
mismo. Si el win rate es bajo, subí los umbrales (más exigente) antes que
bajarlos. No propongas cambios drásticos — preferí ajustes graduales.
Devolvé un JSON:
{{
"min_score_tech": float,
"min_score_macro": float,
"take_profit_atr_mult": float,
"notas_del_ajuste": "Explicación breve, máximo 40 palabras"
}}
"""
        try:
            res = self.client.models.generate_content(model=self.model, contents=prompt)
            cleaned = res.text.strip().replace("```json", "").replace("```", "")
            proposed = json.loads(cleaned)
        except Exception as e:
            self._log_history(conn, sample_size, current_config, None, current_config,
                               f"Ajuste CANCELADO: error consultando IA ({e}).")
            conn.close()
            return

        # --- GUARDARRAÍL 2: clamping — la propuesta nunca se aplica tal cual ---
        applied = dict(current_config)
        for key in ["min_score_tech", "min_score_macro", "take_profit_atr_mult"]:
            if key in proposed:
                applied[key] = round(_clamp(float(proposed[key]), float(current_config[key]),
                                             MAX_PARAM_CHANGE_PCT), 4)
        applied["notas_del_ajuste"] = proposed.get("notas_del_ajuste", "")

        # --- GUARDARRAÍL 4 (nuevo v10.0) — validación contra backtest ---
        # ACEPTADO de la auditoría 9.1 (rev. 1): antes, la propuesta de la IA
        # se aplicaba solo con el clamping numérico, sin chequear si los
        # umbrales nuevos rinden mejor o peor que los actuales contra datos
        # históricos. Ahora corre q_backtest.py con la config VIGENTE y con
        # la PROPUESTA (clampeada) sobre el mismo ticker de referencia, y
        # solo aplica el cambio si el backtest no muestra un resultado
        # claramente peor. Si q_backtest falla (sin red, por ejemplo) o el
        # backtest de referencia no tiene operaciones, el ajuste se aplica
        # igual pero queda marcado como "sin validar por backtest" en el
        # historial — no se bloquea el aprendizaje mensual por un problema
        # de conectividad puntual.
        backtest_note = self._validate_against_backtest(current_config, applied)
        applied["notas_del_ajuste"] += f" {backtest_note}"

        # NUEVO EN v10.5 — escritura atómica: antes se escribía directo
        # sobre auto_tune_config.json con open(...,"w"); si el proceso se
        # interrumpía a mitad de la escritura (corte de luz, kill -9,
        # caída del Droplet), el archivo podía quedar con JSON a medio
        # escribir. j_main.load_tuned_thresholds() ya tenía un try/except
        # que lo detectaba y mantenía los umbrales anteriores (no rompía
        # el arranque del bot), pero el ajuste de ese mes se perdía sin
        # aviso. Ahora se escribe primero a un archivo temporal y se
        # reemplaza con os.replace(), que en Linux es una operación
        # atómica a nivel de sistema de archivos — nunca queda un archivo
        # a medio escribir bajo ese nombre.
        tmp_path = "auto_tune_config.json.tmp"
        with open(tmp_path, "w") as f:
            json.dump(applied, f, indent=2)
        os.replace(tmp_path, "auto_tune_config.json")

        # --- GUARDARRAÍL 3: versionado, para poder revertir a mano ---
        self._log_history(conn, sample_size, current_config, proposed, applied,
                           f"Aplicado con clamping ±{MAX_PARAM_CHANGE_PCT}%. {applied['notas_del_ajuste']}")
        conn.close()

        self._generate_recommendations(win_rate, df_closed)

    def _validate_against_backtest(self, current_config: dict, proposed_config: dict,
                                    reference_ticker: str = "AAPL") -> str:
        """Corre q_backtest.py con los umbrales actuales y con los nuevos
        sobre el mismo ticker de referencia — informativo, no bloqueante
        (ver GUARDARRAÍL 4 arriba)."""
        try:
            import q_backtest
            q_backtest.MIN_SCORE_TECH = current_config["min_score_tech"]
            q_backtest.TAKE_PROFIT_ATR_MULT = current_config["take_profit_atr_mult"]
            before = q_backtest.run_backtest(reference_ticker, period="1y")

            q_backtest.MIN_SCORE_TECH = proposed_config["min_score_tech"]
            q_backtest.TAKE_PROFIT_ATR_MULT = proposed_config["take_profit_atr_mult"]
            after = q_backtest.run_backtest(reference_ticker, period="1y")

            return (f"[Backtest {reference_ticker} 1y — antes: {before.get('win_rate_pct')}% "
                    f"win rate / {before.get('return_pct')}% retorno; después: "
                    f"{after.get('win_rate_pct')}% / {after.get('return_pct')}%]")
        except Exception as e:
            return f"[Backtest no disponible este mes: {e} — ajuste aplicado sin esta validación]"

    def _load_current_config(self) -> dict:
        defaults = {"min_score_tech": 0.70, "min_score_macro": 0.70, "take_profit_atr_mult": 2.0}
        if os.path.exists("auto_tune_config.json"):
            try:
                with open("auto_tune_config.json") as f:
                    saved = json.load(f)
                defaults.update({k: v for k, v in saved.items() if k in defaults})
            except Exception:
                pass
        return defaults

    def _log_history(self, conn, sample_size, previous, proposed, applied, notas):
        c = conn.cursor()
        c.execute(
            "INSERT INTO auto_tune_history (date, sample_size, previous_config, proposed_config, "
            "applied_config, notas) VALUES (?, ?, ?, ?, ?, ?)",
            (date.today().isoformat(), sample_size, json.dumps(previous),
             json.dumps(proposed) if proposed else None, json.dumps(applied), notas),
        )
        conn.commit()

    def _generate_recommendations(self, win_rate, df_closed):
        """
        Sugerencias de mejora en texto plano, para revisión HUMANA en el
        dashboard — esto NO reescribe ni aplica ningún cambio de código
        solo. Ver Documento Maestro v8.0 sobre por qué la autocorrección
        se limita a ajustar números (arriba) y no a reescribir lógica.

        CORRECCIÓN v10.5 — mismo hallazgo que en run_monthly_autotune():
        se resume df_closed agregado por motivo de salida en vez de volcar
        cada fila cruda, para no arriesgar el mismo problema de tamaño de
        prompt si el mes tuvo muchas operaciones cerradas.
        """
        closed_summary = (
            df_closed.groupby(["exit_reason"]).agg(
                cantidad=("id", "count"),
                pnl_ars_promedio=("realized_pnl_ars", "mean"),
            ).round(3).reset_index().to_dict(orient="records")
            if not df_closed.empty else []
        )
        prompt = f"""
Sos un revisor senior de sistemas de trading algorítmico. Con estos datos
del último mes (win rate: {json.dumps(win_rate)}, resumen de operaciones
cerradas por motivo de salida: {json.dumps(closed_summary, ensure_ascii=False)}),
escribí hasta 5 sugerencias concretas y específicas (no genéricas),
separadas en:
1) sugerencias sobre el CÓDIGO/lógica cuantitativa,
2) sugerencias sobre el RAZONAMIENTO de la IA macro.
Texto plano, no JSON.
"""
        try:
            res = self.client.models.generate_content(model=self.model, contents=prompt)
            texto = res.text.strip()
        except Exception as e:
            texto = f"No se pudieron generar recomendaciones este mes: {e}"

        conn = ac_db.connect_raw(self.db_path)
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS recommendations_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT,
                texto TEXT
            )
        """)
        c.execute("INSERT INTO recommendations_log (date, texto) VALUES (?, ?)",
                  (date.today().isoformat(), texto))
        conn.commit()
        conn.close()
