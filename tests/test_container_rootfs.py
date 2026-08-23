"""Regresiones del endurecimiento del filesystem de los contenedores."""

from pathlib import Path


RAIZ = Path(__file__).resolve().parents[1]


def _compose() -> str:
    return (RAIZ / "docker-compose.yml").read_text(encoding="utf-8")


def _bloque(texto: str, inicio: str, fin: str) -> str:
    return texto.split(inicio, 1)[1].split(fin, 1)[0]


def test_bot_usa_rootfs_de_solo_lectura_y_escrituras_explicitadas():
    fuente = _compose()
    bot = _bloque(fuente, "  bot:\n", "\n  init_permissions:\n")

    assert "    read_only: true\n" in bot
    assert "      - ./data:/app/data\n" in bot
    assert "      - ./.env:/app/.env\n" in bot
    assert "      - ./sre_vector_db:/app/sre_vector_db\n" in bot
    assert "      - ./model_cache:/home/botuser/.cache\n" in bot
    assert "      - /tmp:rw,noexec,nosuid,size=128m\n" in bot
    assert "      - /home/botuser/.config:rw,noexec,nosuid,size=16m\n" in bot


def test_init_prepara_la_cache_para_el_usuario_sin_privilegios():
    fuente = _compose()
    init = _bloque(fuente, "  init_permissions:\n", "\n  sre_vectordb:\n")

    assert "./model_cache:/home/botuser/.cache" in init
    assert "chown -R 1000:1000 /app/data /app/sre_vector_db /home/botuser/.cache" in init
    assert "touch /app/data/chroma.log" in init
    assert "chmod 640 /app/data/chroma.log" in init


def test_dockerfile_no_escribe_bytecode_y_dirige_la_cache_al_volumen():
    fuente = (RAIZ / "Dockerfile").read_text(encoding="utf-8")

    assert "PYTHONDONTWRITEBYTECODE=1" in fuente
    assert "XDG_CACHE_HOME=/home/botuser/.cache" in fuente
    assert "MPLCONFIGDIR=/tmp/matplotlib" in fuente


def test_chroma_usa_rootfs_de_solo_lectura_con_escrituras_aisladas():
    fuente = _compose()
    chroma = _bloque(fuente, "  sre_vectordb:\n", "\nnetworks:\n")

    assert "    read_only: true\n" in chroma
    assert "PYTHONDONTWRITEBYTECODE: \"1\"" in chroma
    assert "ANONYMIZED_TELEMETRY: \"FALSE\"" in chroma
    assert "      - /tmp:rw,noexec,nosuid,size=64m\n" in chroma
    assert "      - /root/.cache:rw,noexec,nosuid,size=16m\n" in chroma
    assert "      - ./sre_vector_db:/chroma/chroma\n" in chroma
    assert "      - ./data/chroma.log:/chroma/chroma.log\n" in chroma
    assert "condition: service_completed_successfully" in chroma
