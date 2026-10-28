"""
s_learning_engine.py — Motor de diagnóstico y aprendizaje (v10.0)

VERSIÓN SEGURA de lo que proponía la auditoría 9.1. Es importante que
entiendas exactamente qué se aceptó y qué se rechazó acá, porque es la
pieza más delicada de todo el proyecto.

LO QUE LA AUDITORÍA PROPONÍA (rechazado): un "LearningEngine" que analiza
las operaciones perdedoras, le pide a Gemini que "genere una propuesta de
ajuste", y ese texto —según el diagrama de flujo del informe— termina
con la IA "actualizando el código del bot" y hallendo un "Push Automático
a Repositorio GitHub" que dispara un despliegue a producción, todo
disparado por un solo comando de voz, sin que una persona revise el
cambio de código antes de que quede corriendo con dinero real.

POR QUÉ SE RECHAZA ESA PARTE: este bot coloca órdenes reales con tu
plata. Dejar que un modelo de lenguaje reescriba su propia lógica de
trading y la despliegue sola, sin que una persona mire el cambio antes,
es el mismo tipo de riesgo que en otras industrias se conoce como
"cambiar el motor del avión en pleno vuelo sin que el piloto lo sepa" —
un texto generado por IA puede sonar razonable y aun así introducir un
bug, romper una salvaguarda existente (el kill switch, el hurdle, el
límite de riesgo), o directamente no hacer lo que dice que hace. Ya se
había tomado esta misma decisión en una revisión anterior del proyecto,
y se sostiene acá con más razón todavía, ahora que además hay CI/CD
automático — automatizar el despliegue de un cambio ya revisado por vos
es bueno; automatizar también la GENERACIÓN de ese cambio sin que nadie
lo mire es la parte riesgosa.

LO QUE SÍ SE IMPLEMENTA (la forma segura de lograr el mismo objetivo que
vos pediste — que el bot aprenda de sus errores y vos puedas mejorar el
código junto conmigo): este motor arma un DIAGNÓSTICO en texto plano,
legible, de qué salió mal y en qué archivo — pero nunca escribe ni una
línea de código Python, y nunca toca GitHub. Vos bajás ese diagnóstico
(o me lo pegás acá en el chat, o lo subís como archivo), lo charlamos
juntos, decidimos el cambio, yo te ayudo a escribirlo, y recién ahí se
sube a GitHub — con vos habiendo visto y aprobado el cambio real, no una
promesa de que "va a mejorar los parámetros".
"""

import os
import json
import logging
import sqlite3
from datetime import date
from google import genai
import ac_db  # NUEVO EN v15.0 — conexión SQLite única (WAL + timeout)

logger = logging.getLogger("learning_engine")
DB_PATH = os.getenv("DB_PATH", "data/trading_system.db")


class LearningEngine:
    def __init__(self, notifier):
        self.notifier = notifier
        self.client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
        self.model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")

    def _init_table(self):
        conn = ac_db.connect_raw()
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS learning_diagnostics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT,
                diagnostico_json TEXT
            )
        """)
        conn.commit()
        conn.close()

    def analyze_and_diagnose(self):
        """
        Se corre semanalmente (ver scheduler en j_main.py). Junta las
        últimas operaciones perdedoras y le pide a Gemini un diagnóstico
        POR ARCHIVO — no código, un texto explicando qué mirar y por
        qué — que queda guardado para que vos lo revises cuando quieras,
        conmigo, en el chat.
        """
        conn = ac_db.connect_raw()
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM closed_trades WHERE exit_reason='STOP_LOSS' ORDER BY id DESC LIMIT 10")
        losses = [dict(r) for r in c.fetchall()]
        conn.close()

        if not losses:
            return None

        prompt = f"""
Sos un revisor senior de sistemas de trading algorítmico. Analizá estas
operaciones que cerraron en pérdida (stop-loss):

{json.dumps(losses, indent=2, ensure_ascii=False)}

Generá un DIAGNÓSTICO en JSON — nunca código, solo explicación en texto
para que una persona (no un sistema) decida qué cambiar:
{{
  "resumen": "una oración sobre el patrón general encontrado",
  "hallazgos_por_archivo": [
    {{"archivo": "nombre_del_archivo.py", "que_se_observa": "...", "hipotesis": "...", "sugerencia_para_charlar": "..."}}
  ]
}}
Sé específico y basate en los datos — no generes hallazgos genéricos.
"""
        try:
            res = self.client.models.generate_content(model=self.model, contents=prompt)
            diagnostico = json.loads(res.text.strip().replace("```json", "").replace("```", ""))
        except Exception as e:
            logger.error("Error en motor de diagnóstico: %s", e)
            return None

        self._init_table()
        conn = ac_db.connect_raw()
        c = conn.cursor()
        c.execute("INSERT INTO learning_diagnostics (date, diagnostico_json) VALUES (?, ?)",
                  (date.today().isoformat(), json.dumps(diagnostico, ensure_ascii=False)))
        conn.commit()
        conn.close()

        resumen = diagnostico.get("resumen", "Ver detalle en el dashboard.")
        self.notifier.send_telegram(
            f"🔎 *DIAGNÓSTICO SEMANAL DISPONIBLE*\n{resumen}\n\n"
            "Esto es un análisis para que lo revisemos juntos en el chat con Claude — "
            "el bot NO va a cambiar ningún archivo solo. Bajalo del dashboard "
            "(/api/learning-logs) cuando quieras y lo charlamos."
        )
        return diagnostico
