"""
u_aiops_watcher.py — Detección Predictiva de Anomalías con Isolation Forest
(NUEVO EN v12.0, Instrucción 6).

Corre en un hilo independiente (no bloquea el loop principal de
j_main.py), alimentado por métricas continuas: latencia de red hacia PPI,
consumo de RAM del proceso, y desviación de slippage observada en las
últimas operaciones. Si el modelo marca un punto como anómalo (-1),
activa preventivamente el circuito del cliente PPI ANTES de que ocurra
una caída del broker o una pérdida de capital.

LÍMITE HONESTO: Isolation Forest necesita un mínimo de datos de
entrenamiento para no marcar todo como anómalo al arrancar en frío. Este
watcher arranca con un set de entrenamiento sintético conservador (rangos
normales declarados por el usuario en el .env) y se re-entrena
periódicamente con las métricas reales acumuladas — no pretende tener
"aprendizaje" desde el primer minuto.
"""

import logging
import os
import threading
import time
from collections import deque
from typing import Callable, Optional

logger = logging.getLogger("aiops_watcher")

AIOPS_ENABLED = os.getenv("AIOPS_ENABLED", "true").lower() == "true"
AIOPS_CONTAMINATION = float(os.getenv("AIOPS_CONTAMINATION", "0.05"))
AIOPS_POLL_INTERVAL_SECONDS = int(os.getenv("AIOPS_POLL_INTERVAL_SECONDS", "30"))
AIOPS_RETRAIN_EVERY_N_SAMPLES = int(os.getenv("AIOPS_RETRAIN_EVERY_N_SAMPLES", "200"))
AIOPS_MIN_TRAINING_SAMPLES = int(os.getenv("AIOPS_MIN_TRAINING_SAMPLES", "20"))

# Rangos "normales" declarados para el arranque en frío (Instrucción 6):
# latencia_ms, uso_ram_mb, slippage_pct. Se pueden ajustar por .env sin
# tocar código si la infraestructura real difiere de estos valores default.
_NORMAL_LATENCY_MS = float(os.getenv("AIOPS_NORMAL_LATENCY_MS", "150"))
_NORMAL_RAM_MB = float(os.getenv("AIOPS_NORMAL_RAM_MB", "300"))
_NORMAL_SLIPPAGE_PCT = float(os.getenv("AIOPS_NORMAL_SLIPPAGE_PCT", "0.05"))


SCALPING_COST_VALIDATOR_ENABLED = os.getenv("SCALPING_COST_VALIDATOR_ENABLED", "true").lower() == "true"
MIN_SCALPING_NET_MARGIN_PCT = float(os.getenv("MIN_SCALPING_NET_MARGIN_PCT", "0.5")) / 100


def validate_scalping_viability(recent_trades: list, min_net_profit_margin: float = MIN_SCALPING_NET_MARGIN_PCT) -> bool:
    """
    NUEVO EN v13.0 — Debilidad de la Autoauditoría FODA de v12.0
    ("Scalping sin validar contra la estructura real de costos", sección F
    del Documento Maestro) convertida en código, tal como pide
    Auditoria_version_12.pdf sección 4.2 ("Cost-Validator Halt").

    Revisa las últimas operaciones de scalping YA CERRADAS (con PnL real,
    no proyectado). Si el margen neto promedio (después de comisiones e
    IVA reales, no la estimación previa a la orden) cae por debajo de
    min_net_profit_margin, devuelve False — j_main.py usa este resultado
    para frenar temporalmente las nuevas entradas en modo scalping hasta
    que la estructura de costos vuelva a dejar margen.

    recent_trades: lista de dicts con al menos 'entry_price', 'exit_price'
    y opcionalmente 'real_cost_pct' (si no viene, se usa 1.5% como
    estimación conservadora del costo redondo real de ida y vuelta).

    Con menos de 5 operaciones cerradas todavía no hay señal confiable —
    se permite operar (True) en vez de frenar con datos insuficientes.
    """
    if not SCALPING_COST_VALIDATOR_ENABLED:
        return True
    if not recent_trades or len(recent_trades) < 5:
        return True
    net_margins = []
    for trade in recent_trades:
        entry = trade.get("entry_price") or 0
        exit_p = trade.get("exit_price") or 0
        if entry <= 0:
            continue
        gross_profit_pct = (exit_p - entry) / entry
        real_cost_pct = trade.get("real_cost_pct", 0.015)
        net_margins.append(gross_profit_pct - real_cost_pct)
    if not net_margins:
        return True
    avg_net_margin = sum(net_margins) / len(net_margins)
    if avg_net_margin < min_net_profit_margin:
        logger.warning(
            "Cost-Validator Halt (scalping): margen neto promedio de las últimas %s operaciones "
            "(%.4f%%) por debajo del mínimo exigido (%.4f%%) — se frenan nuevas entradas de scalping "
            "hasta la próxima revisión.", len(net_margins), avg_net_margin * 100, min_net_profit_margin * 100,
        )
        return False
    return True


class AIOpsWatcher:
    def __init__(self, on_anomaly: Callable[[str], None], metrics_fn: Callable[[], Optional[dict]]):
        """
        on_anomaly: callback invocado con una razón legible cuando se detecta
            anomalía (ej. lambda reason: ppi_client._circuit_record_failure()
            o cualquier función que active el circuit breaker/notifique).
        metrics_fn: función que devuelve el dict de métricas actuales
            {"latency_ms": ..., "ram_mb": ..., "slippage_pct": ...} o None
            si todavía no hay datos suficientes en este ciclo (el watcher
            simplemente espera al próximo poll, no inventa un valor).
        """
        self.on_anomaly = on_anomaly
        self.metrics_fn = metrics_fn
        self._history = deque(maxlen=2000)
        self._model = None
        self._thread: Optional[threading.Thread] = None
        self._stop_flag = threading.Event()

    def _train_initial_model(self):
        try:
            from sklearn.ensemble import IsolationForest
            import numpy as np
        except ImportError:
            logger.warning(
                "scikit-learn no está instalado — AIOps watcher deshabilitado "
                "(pip install scikit-learn para habilitarlo)."
            )
            return None
        # Set sintético conservador alrededor de los rangos normales
        # declarados, para no arrancar en frío marcando todo como anómalo.
        base = np.array([[_NORMAL_LATENCY_MS, _NORMAL_RAM_MB, _NORMAL_SLIPPAGE_PCT]])
        synthetic = base + np.random.normal(0, [10, 15, 0.01], size=(50, 3))
        synthetic = np.clip(synthetic, 0, None)
        model = IsolationForest(contamination=AIOPS_CONTAMINATION, random_state=42)
        model.fit(synthetic)
        return model

    def _retrain_with_history(self):
        try:
            from sklearn.ensemble import IsolationForest
            import numpy as np
        except ImportError:
            return
        if len(self._history) < AIOPS_MIN_TRAINING_SAMPLES:
            return
        data = np.array(self._history)
        model = IsolationForest(contamination=AIOPS_CONTAMINATION, random_state=42)
        model.fit(data)
        self._model = model
        logger.info("AIOps: modelo re-entrenado con %s muestras reales acumuladas.", len(self._history))

    def _loop(self):
        try:
            import numpy as np
        except ImportError:
            return
        self._model = self._train_initial_model()
        if self._model is None:
            return
        samples_since_retrain = 0
        while not self._stop_flag.is_set():
            metrics = self.metrics_fn()
            if metrics:
                point = [metrics.get("latency_ms", _NORMAL_LATENCY_MS),
                         metrics.get("ram_mb", _NORMAL_RAM_MB),
                         metrics.get("slippage_pct", _NORMAL_SLIPPAGE_PCT)]
                self._history.append(point)
                samples_since_retrain += 1

                status = self._model.predict(np.array([point]))
                if status[0] == -1:
                    reason = (f"AIOps: anomalía detectada (latencia={point[0]:.0f}ms, "
                              f"RAM={point[1]:.0f}MB, slippage={point[2]:.3f}%)")
                    logger.warning(reason)
                    try:
                        self.on_anomaly(reason)
                    except Exception as e:
                        logger.error("Callback on_anomaly falló: %s", e)

                if samples_since_retrain >= AIOPS_RETRAIN_EVERY_N_SAMPLES:
                    self._retrain_with_history()
                    samples_since_retrain = 0

            self._stop_flag.wait(AIOPS_POLL_INTERVAL_SECONDS)

    def start(self):
        if not AIOPS_ENABLED:
            logger.info("AIOPS_ENABLED=false — watcher de anomalías no arranca.")
            return
        self._thread = threading.Thread(target=self._loop, name="aiops_watcher", daemon=True)
        self._thread.start()
        logger.info("AIOps watcher iniciado (hilo independiente, polling cada %ss).",
                    AIOPS_POLL_INTERVAL_SECONDS)

    def stop(self):
        self._stop_flag.set()
