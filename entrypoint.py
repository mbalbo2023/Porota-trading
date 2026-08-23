"""Supervisor del dashboard y del motor de trading dentro del contenedor.

El dashboard permanece disponible durante toda la vida del contenedor. Si la
política automática BYMA está activa, j_main.py se crea solo dentro de la
ventana bursátil y se termina ordenadamente después del resumen de cierre.
"""

import os
import signal
import subprocess
import sys
import time

from dotenv import load_dotenv

load_dotenv()

PID_FILE = os.getenv("J_MAIN_PID_FILE", "data/j_main.pid")
SUPERVISOR_POLL_SECONDS = 2
BOT_RESTART_COOLDOWN_SECONDS = 30


def _terminate(proc, name, timeout=15):
    if proc is None or proc.poll() is not None:
        return
    print(f"entrypoint: enviando SIGTERM a {name} (pid={proc.pid})...", flush=True)
    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        print(f"entrypoint: {name} no cerró a tiempo, forzando SIGKILL.", flush=True)
        proc.kill()
        proc.wait()


def _clear_pidfile():
    try:
        os.remove(PID_FILE)
    except OSError:
        pass


def _start_bot():
    proc = subprocess.Popen([sys.executable, "j_main.py"])
    try:
        with open(PID_FILE, "w", encoding="utf-8") as f:
            f.write(str(proc.pid))
    except OSError as e:
        print("entrypoint: no se pudo escribir el pidfile "
              f"({e}) — no bloquea el arranque.", flush=True)
    return proc


def _notify_telegram(message):
    """Avisa sin convertir una caída de Telegram en una caída del supervisor."""

    try:
        from b_notifiers import MultiChannelNotifier
        enviado = MultiChannelNotifier().send_telegram(message)
        if not enviado:
            print("entrypoint: Telegram no confirmó la notificación; "
                  "el ciclo continúa.", flush=True)
    except Exception as e:
        print(f"entrypoint: no se pudo avisar por Telegram ({e}); "
              "el ciclo continúa.", flush=True)


def _validate_configuration():
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
        print("Ningún proceso se levantó. Corregí el .env y reintentá.",
              flush=True)
        raise SystemExit(2)


def main():
    os.makedirs(os.path.dirname(PID_FILE) or ".", exist_ok=True)
    _validate_configuration()

    import al_market_startup as market_startup

    auto_enabled = market_startup.AUTO_START_ENABLED
    print("Configuración validada. Levantando dashboard.", flush=True)
    dashboard_proc = subprocess.Popen([sys.executable, "o_dashboard.py"])
    state = {"bot": None, "stopping": False}
    last_bot_exit = 0.0
    last_reason = None

    if not auto_enabled:
        print("Política manual: levantando motor de trading.", flush=True)
        state["bot"] = _start_bot()

    def _forward_signal(signum, frame):
        state["stopping"] = True
        _terminate(state["bot"], "j_main.py")
        _terminate(dashboard_proc, "o_dashboard.py")

    signal.signal(signal.SIGTERM, _forward_signal)
    signal.signal(signal.SIGINT, _forward_signal)

    exit_code = 0
    try:
        while not state["stopping"]:
            if dashboard_proc.poll() is not None:
                exit_code = dashboard_proc.returncode or 0
                print("entrypoint: o_dashboard.py terminó "
                      f"(code={exit_code}) — cerrando el motor.", flush=True)
                _terminate(state["bot"], "j_main.py")
                break

            bot_proc = state["bot"]
            if not auto_enabled:
                if bot_proc.poll() is not None:
                    exit_code = bot_proc.returncode or 0
                    print("entrypoint: j_main.py terminó "
                          f"(code={exit_code}) — cerrando dashboard también.",
                          flush=True)
                    _terminate(dashboard_proc, "o_dashboard.py")
                    break
                time.sleep(SUPERVISOR_POLL_SECONDS)
                continue

            motor_activo, motivo = market_startup.motor_debe_estar_activo()
            listo_para_iniciar, _ = market_startup.evaluar_ventana()

            if bot_proc is not None and bot_proc.poll() is not None:
                codigo = bot_proc.returncode or 0
                print(f"entrypoint: j_main.py terminó (code={codigo}). "
                      "El dashboard sigue disponible.", flush=True)
                state["bot"] = None
                _clear_pidfile()
                last_bot_exit = time.monotonic()
                bot_proc = None

            if state["bot"] is not None and not motor_activo:
                _notify_telegram(
                    "🌙 *RUEDA FINALIZADA*\n"
                    "El motor de trading completó el cierre y entra en "
                    "hibernación. El dashboard continúa disponible.")
                _terminate(state["bot"], "j_main.py")
                state["bot"] = None
                _clear_pidfile()
                last_bot_exit = time.monotonic()
                print(f"entrypoint: {motivo}.", flush=True)

            puede_reiniciar = (
                time.monotonic() - last_bot_exit >= BOT_RESTART_COOLDOWN_SECONDS)
            if (state["bot"] is None and motor_activo and listo_para_iniciar
                    and puede_reiniciar):
                _notify_telegram(
                    "🟢 *INICIANDO SESIÓN BURSÁTIL*\n"
                    "El calendario BYMA habilitó la preparación automática "
                    "del motor de trading.")
                state["bot"] = _start_bot()
                print("entrypoint: motor de trading iniciado por calendario BYMA.",
                      flush=True)
                last_reason = None
            elif state["bot"] is None and motivo != last_reason:
                print(f"entrypoint: {motivo}. Dashboard disponible; "
                      "motor hibernado.", flush=True)
                last_reason = motivo

            time.sleep(SUPERVISOR_POLL_SECONDS)
    finally:
        _terminate(state["bot"], "j_main.py")
        _terminate(dashboard_proc, "o_dashboard.py")
        _clear_pidfile()

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
