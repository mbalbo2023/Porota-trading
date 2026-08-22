"""
entrypoint.py — Punto de entrada real del contenedor Docker (NUEVO EN v13.0)

BUG DE INFRAESTRUCTURA REAL ENCONTRADO en el paquete v12.0 (ver bitácora
v13 para el detalle): el Dockerfile de v12.0 tenía CMD ["python", "j_main.py"]
— el contenedor SOLO arrancaba el bot, nunca o_dashboard.py. El propio
Dockerfile define un HEALTHCHECK que le pega a
http://localhost:${DASHBOARD_PORT:-8000}/health, y docker-compose.yml
publica ese mismo puerto — pero nada dentro del contenedor lo servía.
En la práctica, cualquier despliegue con el paquete v12.0 tal cual tenía
el dashboard inalcanzable Y el healthcheck en fallo permanente (lo que,
con Docker configurado en modo Fail-Fast, podía terminar reiniciando el
contenedor en loop indefinidamente sin que el bot llegara a operar).

Este archivo reemplaza al CMD directo: arranca o_dashboard.py y j_main.py
como dos subprocesos dentro del mismo contenedor (documentado como "un
único contenedor, dos procesos" en el Documento Maestro — se evaluó pasar
a dos contenedores separados en docker-compose.yml, pero eso hubiese
significado duplicar la imagen y la carga de dependencias pesadas de
scikit-learn/langgraph/chromadb en un segundo contenedor solo para un
proceso de solo lectura; no se justifica para este tamaño de proyecto).

También resuelve el pedido de "reinicio graceful": entrypoint.py escribe
el PID de j_main.py en un pidfile (ver PID_FILE) para que, si en algún
momento hace falta reiniciar el bot desde afuera (por ejemplo a mano, por
SSH), se le pueda mandar SIGTERM directamente a ESE proceso — j_main.py ya
sabe cerrar ordenadamente (ver _handle_shutdown_signal en j_main.py). Si
cualquiera de los dos procesos termina (por error o por un shutdown
ordenado), entrypoint.py termina el otro y sale con el mismo código de
salida — con `restart: unless-stopped` en docker-compose.yml, Docker
relanza el contenedor completo, releyendo el .env desde cero.
"""

import os
import signal
import subprocess
import sys
import time

# ---------------------------------------------------------------------- #
# NUEVO EN v14.0 — CARGA DE CONFIGURACIÓN ANTES QUE CUALQUIER OTRA COSA
# ---------------------------------------------------------------------- #
# Esto tiene que ejecutarse ANTES de importar cualquier módulo del proyecto.
# Motivo (bug latente encontrado al corregir docker-compose.yml en v14.0):
# varios módulos leen os.getenv() a nivel de módulo, es decir en el momento
# en que se los importa. Hasta v13.0 eso funcionaba de casualidad, porque
# docker-compose inyectaba todo el .env en el entorno del contenedor con
# `env_file:`, así que las variables ya estaban ahí antes de que arrancara
# Python. Al sacar `env_file` (ver el comentario largo en docker-compose.yml
# sobre por qué había que sacarlo), esa red desaparece: si no se carga el
# .env acá arriba, los módulos importados leerían sus valores por defecto y
# el bot arrancaría, silenciosamente, con la configuración equivocada.
from dotenv import load_dotenv
load_dotenv()


PID_FILE = os.getenv("J_MAIN_PID_FILE", "data/j_main.pid")


def _terminate(proc, name, timeout=15):
    if proc.poll() is not None:
        return
    print(f"entrypoint: enviando SIGTERM a {name} (pid={proc.pid})...", flush=True)
    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        print(f"entrypoint: {name} no cerró a tiempo, forzando SIGKILL.", flush=True)
        proc.kill()


def main():
    os.makedirs(os.path.dirname(PID_FILE) or ".", exist_ok=True)

    # =====================================================================
    # NUEVO EN v16.2 — VALIDAR CREDENCIALES ANTES DE LEVANTAR NADA
    # =====================================================================
    # aa_env_guard.validar_secretos_criticos() existía, estaba completa y era
    # correcta —variables obligatorias, longitud mínima por credencial,
    # detección de valores de plantilla, validación de ENVIRONMENT— y no la
    # llamaba ningún módulo del paquete. El hallazgo que la bitácora daba por
    # cerrado ("se suma verificación de longitud mínima antes de permitir el
    # arranque") quedó implementado a medias: se escribió la verificación, no
    # el "antes de permitir el arranque".
    #
    # La consecuencia práctica: el código SECRETOS_FALTANTES del nivel 0 del
    # portón no existía en tiempo de ejecución. Un token de Telegram truncado
    # al copiar no se detectaba al arrancar sino cuando hacía falta mandar el
    # aviso de un kill switch — el peor momento posible para descubrirlo.
    #
    # Va acá, en el entrypoint, y no dentro de cada proceso, porque este es el
    # único lugar por el que pasan los dos.
    try:
        import aa_env_guard as env_guard
        problemas = env_guard.validar_secretos_criticos()
    except Exception as e:
        print(f"No se pudo validar la configuración: {e}", flush=True)
        raise SystemExit(2)

    try:
        import ay_dashboard_auth as dashboard_auth
        problemas = list(problemas) + dashboard_auth.validar_configuracion()
    except Exception as e:
        print(f"No se pudo validar la configuración del panel: {e}", flush=True)
        raise SystemExit(2)

    if problemas:
        print("=" * 72, flush=True)
        print("NO SE PUEDE ARRANCAR. Problemas de configuración:", flush=True)
        for problema in problemas:
            print("  -", problema, flush=True)
        print("=" * 72, flush=True)
        print("Ninguno de los dos procesos se levantó. Corregí el .env y "
              "reintentá: es preferible que el contenedor no arranque a que "
              "arranque a medias y descubra el faltante en el peor momento.",
              flush=True)
        raise SystemExit(2)

    print("Configuración validada. Levantando panel y bot.", flush=True)
    dashboard_proc = subprocess.Popen([sys.executable, "o_dashboard.py"])
    bot_proc = subprocess.Popen([sys.executable, "j_main.py"])

    try:
        with open(PID_FILE, "w", encoding="utf-8") as f:
            f.write(str(bot_proc.pid))
    except OSError as e:
        print(f"entrypoint: no se pudo escribir el pidfile ({e}) — no bloquea el arranque.", flush=True)

    exit_code = 0
    stopping = {"flag": False}

    def _forward_signal(signum, frame):
        # Reenvía la señal a ambos hijos — así `docker stop` (que manda
        # SIGTERM al proceso 1, este mismo script) también dispara el
        # cierre ordenado de j_main.py en vez de matarlo de golpe.
        stopping["flag"] = True
        for proc, name in ((bot_proc, "j_main.py"), (dashboard_proc, "o_dashboard.py")):
            _terminate(proc, name)

    signal.signal(signal.SIGTERM, _forward_signal)
    signal.signal(signal.SIGINT, _forward_signal)

    try:
        while True:
            if stopping["flag"]:
                break
            if bot_proc.poll() is not None:
                exit_code = bot_proc.returncode or 0
                print(f"entrypoint: j_main.py terminó (code={exit_code}) — cerrando dashboard también.",
                      flush=True)
                _terminate(dashboard_proc, "o_dashboard.py")
                break
            if dashboard_proc.poll() is not None:
                exit_code = dashboard_proc.returncode or 0
                print(f"entrypoint: o_dashboard.py terminó (code={exit_code}) — cerrando el bot también.",
                      flush=True)
                _terminate(bot_proc, "j_main.py")
                break
            time.sleep(2)
    finally:
        try:
            os.remove(PID_FILE)
        except OSError:
            pass

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
