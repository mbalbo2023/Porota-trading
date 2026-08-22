"""
r_clear_kill_switch.py — Liberar el kill switch a mano (v10.5)

Se corre SOLO cuando ya revisaste qué pasó (mirá el dashboard o
preguntame a mí con los logs) y decidís que el bot puede volver a
operar. Nunca se llama solo desde dentro del bot — a propósito.

AMPLIADO EN v10.5 (segunda revisión): una auditoría externa pidió
requerir "doble factor de autenticación" acá. Se evaluó y se descarta esa
forma concreta — este script no es un servicio de red, es un comando que
solo se puede correr con acceso por SSH al servidor (ya protegido por
clave SSH, Fail2Ban y el usuario del sistema operativo — capas que están
fuera del control del propio bot y son las que realmente importan acá).
Pedirle 2FA/JWT a un script de línea de comandos que ya requiere haber
entrado por SSH sería una capa decorativa, no una protección real
adicional. Lo que SÍ es una mejora real y barata: que el script no libere
el corte a ciegas — ahora muestra el motivo por el que se activó y pide
una confirmación explícita antes de liberarlo, para que no se limpie por
error (por ejemplo, al correr el comando equivocado de una lista de
comandos guardados).

Uso: parar el bot, correr esto, prender el bot de nuevo.
    docker compose stop bot
    python3 r_clear_kill_switch.py
    docker compose start bot
"""

import p_risk_guardian as risk_guardian

if __name__ == "__main__":
    risk_guardian.check_persisted_halt_on_startup()
    if not risk_guardian.is_halted():
        print("El kill switch no está activo — no hay nada que liberar.")
    else:
        print(f"Motivo del corte: {risk_guardian.halt_reason()}")
        respuesta = input("¿Ya revisaste este motivo y confirmás que el bot puede volver a "
                          "operar? Escribí SI para confirmar: ")
        if respuesta.strip().upper() == "SI":
            risk_guardian.clear_halt()
            print("Kill switch liberado. Reiniciá el servicio (docker compose start bot) para retomar.")
        else:
            print("Cancelado — el kill switch sigue activo.")
