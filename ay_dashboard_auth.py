"""
ay_dashboard_auth.py — Autenticación del panel (v16.2)

QUÉ CAMBIA
----------
Hasta esta versión el panel se protegía con un único token que viajaba en la
URL o en un encabezado. Funciona, pero tiene dos problemas prácticos: una URL
con el token adentro queda en el historial del navegador y en los logs de
cualquier proxy intermedio, y no hay forma de saber quién entró.

Esta versión agrega un usuario y una contraseña de verdad, con sesión por
cookie, y mantiene el token bearer para el acceso programático (healthchecks,
scripts, el propio CI). Los dos caminos conviven porque cubren usos distintos:
una persona con un navegador quiere una pantalla de login; un healthcheck de
Docker quiere un encabezado.

MODELO DE AMENAZA, DECLARADO
----------------------------
Esto es autenticación para UN usuario en un panel que no debería estar
expuesto a internet. No es un sistema de identidad: no hay registro, ni
recuperación de contraseña, ni roles. La defensa real del panel son tres
capas y esta es la tercera:

  1. El puerto se publica solo en el loopback del host (docker-compose).
  2. Un reverse proxy con TLS es el único que lo expone hacia afuera.
  3. Esta autenticación.

Si alguien saltea las dos primeras y publica el puerto directo, esta capa es
lo único que queda — y por eso existe, no porque alcance sola.

CONTRASEÑAS
-----------
Se guardan hasheadas con PBKDF2-HMAC-SHA256 y sal aleatoria por instalación.
Nunca en texto plano, ni siquiera en el .env: la variable de entorno lleva el
hash. Hay una excepción documentada y deliberada para el entorno de pruebas
—DASHBOARD_PASSWORD en texto plano— porque exigir un hash para arrancar un
sandbox agrega fricción sin agregar seguridad real; si esa variable está
presente en un entorno de producción, el sistema lo AVISA en cada arranque.

La comparación de credenciales usa `secrets.compare_digest`, que tarda lo
mismo con una contraseña que difiere en el primer carácter que con una que
difiere en el último. Una comparación normal filtra información por el tiempo
que tarda en fallar.
"""

import base64
import hashlib
import hmac
import logging
import os
import secrets
import threading
import time
from typing import Optional, Dict

logger = logging.getLogger("dashboard_auth")

USUARIO = os.getenv("DASHBOARD_USER", "porota").strip()
PASSWORD_HASH = os.getenv("DASHBOARD_PASSWORD_HASH", "").strip()
PASSWORD_PLANA = os.getenv("DASHBOARD_PASSWORD", "").strip()
TOKEN_BEARER = os.getenv("DASHBOARD_ACCESS_TOKEN", "").strip()
ENTORNO = os.getenv("ENVIRONMENT", "SANDBOX").strip().upper()

SESION_HORAS = float(os.getenv("DASHBOARD_SESSION_HOURS", "12"))
MAX_INTENTOS = int(os.getenv("DASHBOARD_MAX_LOGIN_ATTEMPTS", "5"))
BLOQUEO_MINUTOS = float(os.getenv("DASHBOARD_LOCKOUT_MINUTES", "15"))

ITERACIONES = 240_000

_sesiones: Dict[str, float] = {}
_intentos: Dict[str, list] = {}
_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------

def hashear(password: str, sal: Optional[bytes] = None) -> str:
    """Devuelve una cadena portable: pbkdf2$iteraciones$sal$hash.

    Se puede generar desde la línea de comandos para cargar el resultado en
    DASHBOARD_PASSWORD_HASH:

        python -c "import ay_dashboard_auth as a; print(a.hashear('tu-clave'))"
    """
    sal = sal or secrets.token_bytes(16)
    derivado = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), sal, ITERACIONES)
    return "pbkdf2${}${}${}".format(
        ITERACIONES,
        base64.b64encode(sal).decode("ascii"),
        base64.b64encode(derivado).decode("ascii"),
    )


def verificar_hash(password: str, almacenado: str) -> bool:
    try:
        algoritmo, iteraciones, sal_b64, hash_b64 = almacenado.split("$")
        if algoritmo != "pbkdf2":
            return False
        sal = base64.b64decode(sal_b64)
        esperado = base64.b64decode(hash_b64)
        calculado = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), sal, int(iteraciones))
        return hmac.compare_digest(calculado, esperado)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Arranque: fallar cerrado
# ---------------------------------------------------------------------------

def validar_configuracion() -> list:
    """Problemas que impiden arrancar el panel de forma segura.

    Devuelve una lista de textos. Vacía significa que está bien.

    El criterio es fallar CERRADO: antes, si el token quedaba vacío, el panel
    arrancaba sin ninguna protección. Un panel sin protección que arranca es
    peor que un panel que no arranca, porque el segundo se nota enseguida y el
    primero no se nota nunca.
    """
    problemas = []
    if not (PASSWORD_HASH or PASSWORD_PLANA):
        problemas.append(
            "No hay contraseña de panel configurada. Definí DASHBOARD_PASSWORD_HASH "
            "(recomendado) o DASHBOARD_PASSWORD (solo para pruebas).")
    if not TOKEN_BEARER:
        problemas.append(
            "DASHBOARD_ACCESS_TOKEN está vacío. Sin token no hay acceso programático "
            "y el healthcheck autenticado no puede funcionar.")
    if TOKEN_BEARER and len(TOKEN_BEARER) < 24:
        problemas.append(
            f"DASHBOARD_ACCESS_TOKEN tiene {len(TOKEN_BEARER)} caracteres. Es corto "
            "para ser la única puerta si el panel queda expuesto: usá 32 o más, "
            "generados al azar.")
    if PASSWORD_PLANA and ENTORNO == "PRODUCTION":
        problemas.append(
            "DASHBOARD_PASSWORD está en texto plano y el entorno es PRODUCTION. "
            "Generá el hash con ay_dashboard_auth.hashear() y cargá el resultado "
            "en DASHBOARD_PASSWORD_HASH.")
    return problemas


# ---------------------------------------------------------------------------
# Login y sesiones
# ---------------------------------------------------------------------------

def _bloqueado(origen: str) -> Optional[float]:
    """Minutos que faltan para poder reintentar, o None si no está bloqueado."""
    intentos = _intentos.get(origen, [])
    ventana = time.time() - BLOQUEO_MINUTOS * 60
    recientes = [t for t in intentos if t > ventana]
    _intentos[origen] = recientes
    if len(recientes) >= MAX_INTENTOS:
        return round((recientes[0] + BLOQUEO_MINUTOS * 60 - time.time()) / 60, 1)
    return None


def login(usuario: str, password: str, origen: str = "desconocido") -> Optional[str]:
    """Devuelve un identificador de sesión, o None si las credenciales fallan.

    El bloqueo por intentos es por origen y con ventana móvil. No pretende
    frenar a un atacante decidido —para eso está el proxy y el firewall— pero
    convierte un ataque de diccionario contra una contraseña débil en algo que
    tarda semanas en vez de minutos.
    """
    with _lock:
        faltan = _bloqueado(origen)
        if faltan is not None:
            logger.warning("Login bloqueado para %s: quedan %.1f minutos.", origen, faltan)
            return None

        usuario_ok = hmac.compare_digest((usuario or "").strip(), USUARIO)

        if PASSWORD_HASH:
            password_ok = verificar_hash(password or "", PASSWORD_HASH)
        elif PASSWORD_PLANA:
            password_ok = hmac.compare_digest((password or ""), PASSWORD_PLANA)
        else:
            logger.error("Intento de login sin contraseña configurada: se rechaza.")
            return None

        # Las dos comprobaciones se hacen SIEMPRE, y recién después se combinan.
        # Cortar apenas falla el usuario permitiría distinguir "usuario
        # inexistente" de "contraseña incorrecta" por el tiempo de respuesta.
        if not (usuario_ok and password_ok):
            _intentos.setdefault(origen, []).append(time.time())
            logger.warning("Login fallido desde %s (usuario=%r).", origen, usuario)
            return None

        _intentos.pop(origen, None)
        sesion = secrets.token_urlsafe(32)
        _sesiones[sesion] = time.time() + SESION_HORAS * 3600
        logger.info("Login correcto desde %s.", origen)
        return sesion


def sesion_valida(identificador: Optional[str]) -> bool:
    if not identificador:
        return False
    with _lock:
        vence = _sesiones.get(identificador)
        if not vence:
            return False
        if time.time() > vence:
            _sesiones.pop(identificador, None)
            return False
        return True


def cerrar_sesion(identificador: Optional[str]) -> None:
    if identificador:
        with _lock:
            _sesiones.pop(identificador, None)


def token_valido(token: Optional[str]) -> bool:
    """Acceso programático. Mismo criterio de comparación en tiempo constante."""
    if not (token and TOKEN_BEARER):
        return False
    return hmac.compare_digest(token.strip(), TOKEN_BEARER)


def autorizado(*, cookie: Optional[str] = None, encabezado: Optional[str] = None,
               parametro: Optional[str] = None) -> bool:
    """La comprobación única que usa cada ruta del panel.

    Acepta tres caminos, en orden de preferencia:
      · cookie de sesión (una persona con un navegador),
      · encabezado Authorization: Bearer (scripts, healthchecks),
      · parámetro ?token= en la URL — se acepta por compatibilidad, pero deja
        el token en el historial del navegador y en los logs de cualquier
        proxy. Se avisa por log cada vez que se usa.
    """
    if sesion_valida(cookie):
        return True
    if encabezado:
        partes = encabezado.split(None, 1)
        if len(partes) == 2 and partes[0].lower() == "bearer" and token_valido(partes[1]):
            return True
    if parametro and token_valido(parametro):
        logger.info("Acceso al panel con token en la URL. Preferí el encabezado "
                    "Authorization: Bearer — el parámetro queda registrado en el "
                    "historial y en los logs del proxy.")
        return True
    return False


def credenciales_para_documentar() -> dict:
    """Lo que el archivo de acceso y la auditoría tienen que decir, sin
    revelar el secreto completo."""
    return {
        "usuario": USUARIO,
        "password_configurada": bool(PASSWORD_HASH or PASSWORD_PLANA),
        "password_hasheada": bool(PASSWORD_HASH),
        "token_ultimos_4": TOKEN_BEARER[-4:] if len(TOKEN_BEARER) >= 4 else "",
        "sesion_horas": SESION_HORAS,
        "entorno": ENTORNO,
    }
