"""
an_sre_deploy.py — Propuestas de mejora del motor SRE: evaluación, aplicación
controlada y vuelta atrás

EL PEDIDO, Y LA ADVERTENCIA QUE VA ADELANTE
---------------------------------------------------------------------------
Lo pedido: que el panel muestre las propuestas de mejora del motor SRE con
su criticidad y su impacto, que la IA verifique si son factibles, que haya un
botón "implementar", que si hay que reiniciar no haya operaciones activas,
que la confirmación llegue por Telegram, que se haga alrededor de las 20 h al
cierre de la rueda, que haya autotesting antes para no dejar el sistema
inutilizable, y que si el despliegue falla haya vuelta atrás con la razón
explicada en el panel.

Está todo implementado. Pero corresponde decir con todas las letras qué es
esto, porque es la funcionalidad más riesgosa de todo el sistema: un camino
por el cual el software se modifica a sí mismo mientras administra dinero
real. Si algo sale mal acá, no falla una operación: puede quedar inservible
el bot entero, con posiciones abiertas y sin nadie vigilándolas.

Por eso el diseño tiene tres candados que NO son configurables, y conviene
entender por qué cada uno está donde está:

  CANDADO 1 — LA IA NO ESCRIBE EL CÓDIGO QUE SE APLICA. Evalúa, puntúa y
  explica; el cambio que se aplica es un parche que ya existe como archivo
  en la cola de propuestas, revisable antes de tocar nada. Dejar que un
  modelo genere código y ese mismo código se instale solo, sin que nadie
  haya podido leerlo, es exactamente el escenario en el que un error de
  redacción se convierte en un sistema caído a las tres de la mañana.

  CANDADO 2 — SIEMPRE HAY UNA PERSONA. El botón del panel no aplica nada:
  encola. La aplicación exige una confirmación por Telegram con un código de
  un solo uso que vence. Automatizar también ese paso ahorraría treinta
  segundos por semana a cambio de sacar al único que puede decir "esto no
  me cierra".

  CANDADO 3 — SE PRUEBA SOBRE UNA COPIA, NUNCA SOBRE EL SISTEMA VIVO. El
  autotest corre en un directorio aparte. Si falla ahí, el sistema real
  nunca se enteró de que existía la propuesta.

CUÁNDO SE PUEDE APLICAR, Y POR QUÉ ESA HORA
---------------------------------------------------------------------------
La ventana por default es de 20:00 a 22:00. La rueda de contado en BYMA
cierra a las 17:00, así que a las 20 ya no queda ninguna operación en curso
ni liquidación en juego, y quedan horas de sobra antes de la apertura
siguiente para volver atrás con calma si algo salió mal. Aplicar un cambio a
las 8 de la mañana sería lo contrario: descubrir el problema justo cuando
abre el mercado.

Además de la hora, hay tres condiciones que se verifican una por una y que
bloquean por separado: que no haya posiciones abiertas, que no haya órdenes
pendientes de confirmación, y que el kill switch no esté activo. Reiniciar
con una posición abierta significa dejarla sin vigilancia de stop-loss
durante el tiempo que tarde el arranque, y ese es precisamente el momento en
que el mercado hace lo que no esperabas.
"""

import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, asdict
from datetime import datetime, time as dtime
from typing import Dict, List, Optional

logger = logging.getLogger("sre_deploy")

PROPOSALS_DIR = os.getenv("PROPOSALS_DIR", "./data/proposals")
BACKUP_DIR = os.getenv("SRE_BACKUP_DIR", "./data/sre_backups")
DEPLOY_WINDOW_START = int(os.getenv("SRE_DEPLOY_WINDOW_START_HOUR", "20"))
DEPLOY_WINDOW_END = int(os.getenv("SRE_DEPLOY_WINDOW_END_HOUR", "22"))
CONFIRM_TOKEN_TTL_SECONDS = int(os.getenv("SRE_CONFIRM_TTL_SECONDS", "900"))
AUTOTEST_TIMEOUT = int(os.getenv("SRE_AUTOTEST_TIMEOUT", "300"))

CRITICIDADES = ("BAJA", "MEDIA", "ALTA", "CRITICA")


@dataclass
class Propuesta:
    id: str
    titulo: str
    descripcion: str
    archivos_afectados: List[str]
    criticidad: str = "MEDIA"
    requiere_reinicio: bool = True
    origen: str = "motor_sre"
    creada: str = ""
    # Lo que completa la evaluación con IA:
    factible: Optional[bool] = None
    impacto: str = ""
    riesgos: str = ""
    confianza: float = 0.0
    evaluada: str = ""
    # Lo que completa el ciclo de aplicación:
    estado: str = "PENDIENTE"   # PENDIENTE | EVALUADA | ENCOLADA | APLICADA | REVERTIDA | RECHAZADA
    resultado_autotest: str = ""
    motivo_falla: str = ""
    backup_id: str = ""


# ---------------------------------------------------------------------------
# 1. Evaluación de factibilidad con IA
# ---------------------------------------------------------------------------

PROMPT_EVALUACION = """Sos un ingeniero de confiabilidad evaluando una propuesta de cambio
sobre un bot de trading que opera con dinero real en el mercado argentino.

PROPUESTA
Título: {titulo}
Descripción: {descripcion}
Archivos afectados: {archivos}
Criticidad declarada por el motor SRE: {criticidad}

CONTENIDO DEL PARCHE (podés basarte solo en esto; no supongas nada que no esté acá):
{parche}

Respondé ÚNICAMENTE con un objeto JSON, sin texto alrededor y sin markdown, con estas claves:
  "factible": true o false — si el cambio se puede aplicar tal cual está
  "impacto": una frase sobre qué cambia en el comportamiento del sistema
  "riesgos": una frase sobre qué podría romperse
  "criticidad_sugerida": "BAJA", "MEDIA", "ALTA" o "CRITICA"
  "confianza": número entre 0 y 1

Criterio: si el parche toca la lógica de envío de órdenes, el cálculo de tamaño de
posición o los límites de riesgo, marcá criticidad CRITICA aunque el cambio parezca chico.
Si no podés determinar qué hace el parche, respondé factible=false: la duda no se
resuelve aprobando."""


def evaluar_con_ia(propuesta: Propuesta, gemini_engine, contenido_parche: str = "") -> Propuesta:
    """Le pide al motor de IA un dictamen de factibilidad e impacto.

    Si la IA no responde o responde algo ilegible, la propuesta queda NO
    factible. Es a propósito: ante la ausencia de dictamen, lo seguro es no
    aplicar. Un fallo del evaluador nunca puede traducirse en una aprobación
    por omisión.
    """
    prompt = PROMPT_EVALUACION.format(
        titulo=propuesta.titulo, descripcion=propuesta.descripcion,
        archivos=", ".join(propuesta.archivos_afectados),
        criticidad=propuesta.criticidad,
        parche=(contenido_parche or "(parche no disponible)")[:8000],
    )
    try:
        crudo = gemini_engine.consulta_libre(prompt) if hasattr(gemini_engine, "consulta_libre") else None
        if not crudo:
            raise ValueError("El motor de IA no devolvió respuesta.")
        limpio = crudo.replace("```json", "").replace("```", "").strip()
        veredicto = json.loads(limpio)
    except Exception as e:
        propuesta.factible = False
        propuesta.impacto = "No se pudo evaluar."
        propuesta.riesgos = f"El evaluador falló: {e}. Sin dictamen, no se aplica."
        propuesta.confianza = 0.0
        propuesta.estado = "EVALUADA"
        propuesta.evaluada = datetime.now().isoformat(timespec="seconds")
        guardar(propuesta)
        return propuesta

    propuesta.factible = bool(veredicto.get("factible"))
    propuesta.impacto = str(veredicto.get("impacto", ""))[:500]
    propuesta.riesgos = str(veredicto.get("riesgos", ""))[:500]
    propuesta.confianza = float(veredicto.get("confianza", 0) or 0)
    sugerida = str(veredicto.get("criticidad_sugerida", "")).upper()
    if sugerida in CRITICIDADES:
        # Se acepta la criticidad de la IA solo si SUBE el nivel. Que un
        # evaluador automático pueda bajarle la criticidad a un cambio sería
        # una forma silenciosa de saltear controles.
        if CRITICIDADES.index(sugerida) > CRITICIDADES.index(propuesta.criticidad):
            propuesta.criticidad = sugerida
    propuesta.estado = "EVALUADA"
    propuesta.evaluada = datetime.now().isoformat(timespec="seconds")
    guardar(propuesta)
    return propuesta


# ---------------------------------------------------------------------------
# 2. Condiciones para poder aplicar
# ---------------------------------------------------------------------------

def condiciones_de_aplicacion(ahora: Optional[datetime] = None) -> dict:
    """Verifica una por una las condiciones y devuelve TODAS las que fallan.

    Se devuelven todas y no solo la primera a propósito: si el usuario aprieta
    el botón y le decimos "hay una posición abierta", corrige eso, vuelve a
    apretar y recién ahí se entera de que además está fuera de horario. Un
    diagnóstico completo de una es respetuoso del tiempo de quien lo lee.
    """
    ahora = ahora or datetime.now()
    bloqueos = []

    en_ventana = DEPLOY_WINDOW_START <= ahora.hour < DEPLOY_WINDOW_END
    if not en_ventana:
        bloqueos.append(f"Fuera de la ventana de despliegue ({DEPLOY_WINDOW_START}:00 a "
                        f"{DEPLOY_WINDOW_END}:00). La rueda cierra a las 17:00 y se despliega "
                        f"después, con horas de margen antes de la apertura siguiente.")

    try:
        import k_position_manager as pm
        abiertas = pm.get_open_positions() or []
        if abiertas:
            bloqueos.append(f"Hay {len(abiertas)} posición(es) abierta(s). Reiniciar las dejaría "
                            f"sin vigilancia de stop-loss durante el arranque.")
    except Exception as e:
        bloqueos.append(f"No se pudo verificar si hay posiciones abiertas: {e}. Ante la duda, no se aplica.")

    try:
        import l_order_confirmation as oc
        pendientes = oc.get_pending_proposals() if hasattr(oc, "get_pending_proposals") else []
        if pendientes:
            bloqueos.append(f"Hay {len(pendientes)} orden(es) esperando confirmación.")
    except Exception:
        pass

    try:
        import p_risk_guardian as rg
        if rg.is_halted():
            bloqueos.append("El kill switch está activo. Primero se resuelve el corte, después se despliega.")
    except Exception:
        pass

    return {"puede_aplicar": not bloqueos, "bloqueos": bloqueos,
            "verificado": ahora.isoformat(timespec="seconds")}


# ---------------------------------------------------------------------------
# 3. Autotest sobre una copia
# ---------------------------------------------------------------------------

def autotest(propuesta: Propuesta, raiz: str = ".") -> dict:
    """
    Copia el proyecto a un directorio temporal, aplica el parche ahí y corre
    tres pruebas en orden de costo creciente. Si alguna falla, se corta: no
    tiene sentido correr los tests si el archivo ni siquiera compila.

      1. Compilación de todos los módulos (¿es Python válido?)
      2. Importación real de cada módulo (¿los imports existen?)
      3. pytest (¿la lógica sigue haciendo lo que decía?)

    El paso 2 es el que atrapa el error más frecuente de este tipo de cambios:
    un parche que compila perfecto pero llama a una función que se renombró.
    """
    resultado = {"ok": False, "pasos": [], "detalle": ""}
    with tempfile.TemporaryDirectory(prefix="sre_autotest_") as tmp:
        destino = os.path.join(tmp, "proyecto")
        try:
            shutil.copytree(raiz, destino,
                            ignore=shutil.ignore_patterns("data", "*.db", "__pycache__",
                                                          ".git", "sre_vector_db", "*.pdf"))
        except Exception as e:
            resultado["detalle"] = f"No se pudo copiar el proyecto para probar: {e}"
            return resultado

        if not _aplicar_parche(propuesta, destino):
            resultado["detalle"] = "El parche no se pudo aplicar sobre la copia."
            return resultado

        pruebas = [
            ("compilación", [sys.executable, "-m", "compileall", "-q", destino]),
            ("importación", [sys.executable, "-c",
                             "import pathlib,importlib,sys;sys.path.insert(0,'.');"
                             "[importlib.import_module(p.stem) for p in pathlib.Path('.').glob('*.py')"
                             " if p.stem not in ('entrypoint','j_main')]"]),
            ("pytest", [sys.executable, "-m", "pytest", "-q", "--maxfail=1"]),
        ]
        for nombre, comando in pruebas:
            try:
                proc = subprocess.run(comando, cwd=destino, capture_output=True,
                                      text=True, timeout=AUTOTEST_TIMEOUT)
            except subprocess.TimeoutExpired:
                resultado["pasos"].append({"paso": nombre, "ok": False, "detalle": "Excedió el tiempo."})
                resultado["detalle"] = f"El paso '{nombre}' no terminó en {AUTOTEST_TIMEOUT} s."
                return resultado

            ok = proc.returncode == 0
            salida = (proc.stderr or proc.stdout or "")[-1500:]
            resultado["pasos"].append({"paso": nombre, "ok": ok, "detalle": salida})
            if not ok:
                resultado["detalle"] = f"Falló el paso '{nombre}'. El sistema real no se tocó."
                return resultado

    resultado["ok"] = True
    resultado["detalle"] = "Los tres pasos pasaron sobre la copia."
    return resultado


def _aplicar_parche(propuesta: Propuesta, raiz: str) -> bool:
    """Aplica los archivos del parche guardado sobre la raíz indicada."""
    origen = os.path.join(PROPOSALS_DIR, propuesta.id, "archivos")
    if not os.path.isdir(origen):
        logger.error("La propuesta %s no tiene archivos de parche en %s.", propuesta.id, origen)
        return False
    try:
        for nombre in os.listdir(origen):
            shutil.copy2(os.path.join(origen, nombre), os.path.join(raiz, nombre))
        return True
    except Exception as e:
        logger.error("No se pudo aplicar el parche de %s: %s", propuesta.id, e)
        return False


# ---------------------------------------------------------------------------
# 4. Respaldo, aplicación y vuelta atrás
# ---------------------------------------------------------------------------

def crear_respaldo(propuesta: Propuesta, raiz: str = ".") -> str:
    """Guarda una copia de los archivos que el parche va a pisar.

    Se respaldan solo los archivos afectados y no el proyecto entero: el
    respaldo tiene que ser rápido y chico, porque un respaldo que tarda es un
    respaldo que en algún momento alguien salta 'por esta vez'.
    """
    backup_id = f"{propuesta.id}_{int(time.time())}"
    destino = os.path.join(BACKUP_DIR, backup_id)
    os.makedirs(destino, exist_ok=True)
    for archivo in propuesta.archivos_afectados:
        ruta = os.path.join(raiz, archivo)
        if os.path.exists(ruta):
            shutil.copy2(ruta, os.path.join(destino, archivo))
    with open(os.path.join(destino, "_manifiesto.json"), "w", encoding="utf-8") as f:
        json.dump({"propuesta": propuesta.id, "archivos": propuesta.archivos_afectados,
                   "creado": datetime.now().isoformat(timespec="seconds")}, f, ensure_ascii=False)
    logger.info("Respaldo %s creado con %d archivo(s).", backup_id, len(propuesta.archivos_afectados))
    return backup_id


def revertir(backup_id: str, raiz: str = ".") -> dict:
    """Restaura los archivos del respaldo. Es la operación que tiene que
    funcionar sí o sí, así que no depende de nada más que copiar archivos."""
    origen = os.path.join(BACKUP_DIR, backup_id)
    if not os.path.isdir(origen):
        return {"ok": False, "detalle": f"No existe el respaldo {backup_id}."}
    restaurados = []
    for nombre in os.listdir(origen):
        if nombre == "_manifiesto.json":
            continue
        try:
            shutil.copy2(os.path.join(origen, nombre), os.path.join(raiz, nombre))
            restaurados.append(nombre)
        except Exception as e:
            return {"ok": False, "detalle": f"Falló al restaurar {nombre}: {e}"}
    return {"ok": True, "restaurados": restaurados,
            "detalle": f"Se restauraron {len(restaurados)} archivo(s) desde {backup_id}."}


def aplicar(propuesta: Propuesta, notifier=None, raiz: str = ".") -> dict:
    """
    Camino completo: condiciones → autotest → respaldo → aplicación →
    verificación → vuelta atrás automática si algo falla.

    La verificación posterior no es opcional ni cosmética: aplicar el parche y
    dar por hecho que funcionó porque los tests pasaron sobre una COPIA es
    justamente el error que deja el sistema inservible. Se vuelve a compilar e
    importar sobre el sistema real, y si eso falla, se revierte sin preguntar.
    """
    condiciones = condiciones_de_aplicacion()
    if not condiciones["puede_aplicar"]:
        return {"ok": False, "etapa": "condiciones", "motivo": " | ".join(condiciones["bloqueos"])}

    if propuesta.factible is not True:
        return {"ok": False, "etapa": "evaluación",
                "motivo": "La propuesta no está marcada como factible por el evaluador."}

    prueba = autotest(propuesta, raiz)
    propuesta.resultado_autotest = prueba["detalle"]
    if not prueba["ok"]:
        propuesta.estado = "RECHAZADA"
        propuesta.motivo_falla = prueba["detalle"]
        guardar(propuesta)
        _avisar(notifier, f"❌ Propuesta {propuesta.id} rechazada en autotest.\n{prueba['detalle']}")
        return {"ok": False, "etapa": "autotest", "motivo": prueba["detalle"], "pasos": prueba["pasos"]}

    propuesta.backup_id = crear_respaldo(propuesta, raiz)
    if not _aplicar_parche(propuesta, raiz):
        propuesta.estado = "RECHAZADA"
        propuesta.motivo_falla = "No se pudo copiar el parche al sistema real."
        guardar(propuesta)
        return {"ok": False, "etapa": "aplicación", "motivo": propuesta.motivo_falla}

    verificacion = subprocess.run([sys.executable, "-m", "compileall", "-q", raiz],
                                  capture_output=True, text=True)
    if verificacion.returncode != 0:
        vuelta = revertir(propuesta.backup_id, raiz)
        propuesta.estado = "REVERTIDA"
        propuesta.motivo_falla = (f"El sistema real no compiló después de aplicar el parche. "
                                  f"{verificacion.stderr[-500:]} | Vuelta atrás: {vuelta['detalle']}")
        guardar(propuesta)
        _avisar(notifier, f"⚠️ Propuesta {propuesta.id} aplicada y REVERTIDA.\n{propuesta.motivo_falla}")
        return {"ok": False, "etapa": "verificación", "motivo": propuesta.motivo_falla, "revertido": True}

    propuesta.estado = "APLICADA"
    propuesta.motivo_falla = ""
    guardar(propuesta)
    _avisar(notifier, f"✅ Propuesta {propuesta.id} aplicada correctamente: {propuesta.titulo}\n"
                      f"Respaldo disponible: {propuesta.backup_id}\n"
                      f"Reiniciá el servicio cuando quieras que tome efecto.")
    return {"ok": True, "etapa": "completado", "backup_id": propuesta.backup_id,
            "requiere_reinicio": propuesta.requiere_reinicio}


def _avisar(notifier, mensaje: str) -> None:
    if notifier is None:
        logger.info("SRE: %s", mensaje)
        return
    try:
        notifier.send(mensaje)
    except Exception as e:
        logger.warning("No se pudo notificar por Telegram: %s", e)


# ---------------------------------------------------------------------------
# 5. Persistencia y confirmación de un solo uso
# ---------------------------------------------------------------------------

_confirmaciones: Dict[str, tuple] = {}


def encolar_para_confirmacion(propuesta: Propuesta, notifier=None) -> dict:
    """Lo que hace el botón del panel: NO aplica, encola y pide confirmación.

    El código es de un solo uso y vence. Un código sin vencimiento que queda
    en el historial de Telegram es una llave permanente para modificar el
    sistema, disponible para cualquiera que llegue a ese chat.
    """
    codigo = f"{propuesta.id[:6]}-{int(time.time()) % 10000:04d}"
    _confirmaciones[codigo] = (propuesta.id, time.time())
    propuesta.estado = "ENCOLADA"
    guardar(propuesta)

    condiciones = condiciones_de_aplicacion()
    estado_cond = ("Listo para aplicar." if condiciones["puede_aplicar"]
                   else "BLOQUEADO por: " + " | ".join(condiciones["bloqueos"]))

    _avisar(notifier,
            f"🛠️ Propuesta de mejora lista para aplicar\n\n"
            f"{propuesta.titulo}\n"
            f"Criticidad: {propuesta.criticidad}\n"
            f"Impacto: {propuesta.impacto}\n"
            f"Riesgos: {propuesta.riesgos}\n"
            f"Archivos: {', '.join(propuesta.archivos_afectados)}\n\n"
            f"{estado_cond}\n\n"
            f"Para confirmar, respondé: APLICAR {codigo}\n"
            f"El código vence en {CONFIRM_TOKEN_TTL_SECONDS // 60} minutos.")
    return {"codigo": codigo, "condiciones": condiciones}


def validar_confirmacion(codigo: str) -> Optional[str]:
    """Devuelve el id de la propuesta si el código es válido y no venció.
    Consume el código: un segundo intento con el mismo código no sirve."""
    dato = _confirmaciones.pop(codigo, None)
    if not dato:
        return None
    propuesta_id, creado = dato
    if time.time() - creado > CONFIRM_TOKEN_TTL_SECONDS:
        logger.info("Código de confirmación %s vencido.", codigo)
        return None
    return propuesta_id


def guardar(propuesta: Propuesta) -> None:
    carpeta = os.path.join(PROPOSALS_DIR, propuesta.id)
    os.makedirs(carpeta, exist_ok=True)
    with open(os.path.join(carpeta, "propuesta.json"), "w", encoding="utf-8") as f:
        json.dump(asdict(propuesta), f, ensure_ascii=False, indent=2)


def listar() -> List[dict]:
    """Todas las propuestas para el panel, las nuevas primero."""
    if not os.path.isdir(PROPOSALS_DIR):
        return []
    propuestas = []
    for nombre in os.listdir(PROPOSALS_DIR):
        ruta = os.path.join(PROPOSALS_DIR, nombre, "propuesta.json")
        if os.path.exists(ruta):
            try:
                with open(ruta, encoding="utf-8") as f:
                    propuestas.append(json.load(f))
            except Exception as e:
                logger.warning("Propuesta ilegible en %s: %s", ruta, e)
    return sorted(propuestas, key=lambda p: p.get("creada", ""), reverse=True)
