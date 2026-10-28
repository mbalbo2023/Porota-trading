"""Regresiones del endurecimiento del filesystem de los contenedores."""

from pathlib import Path


def test_ci_inspecciona_la_imagen_configurada_y_no_un_tag_viejo(tmp_path):
    import json
    import os
    import subprocess
    import sys
    import yaml
    workflow = yaml.safe_load((RAIZ / ".github/workflows/ci.yml").read_text())
    script = next(step["run"] for step in workflow["jobs"]["docker"]["steps"]
                  if step.get("name") == "La imagen no contiene archivos de entorno")
    log = tmp_path / "docker_calls.jsonl"
    docker = tmp_path / "docker"
    docker.write_text(f"#!{sys.executable}\n" + '''import json, os, sys
args = sys.argv[1:]
with open(os.environ['DOCKER_TEST_LOG'], 'a') as out:
    out.write(json.dumps(args) + '\\n')
if args == ['compose', 'config', '--format', 'json']:
    print(json.dumps({'services': {'bot': {'image': 'porota-test:otra-version'}}}))
elif args[:2] == ['image', 'inspect']:
    assert args[2] == 'porota-test:otra-version'
elif args[:4] == ['run', '--rm', '--entrypoint', 'sh']:
    assert args[4] == 'porota-test:otra-version'
else:
    raise AssertionError(args)
''')
    docker.chmod(0o755)
    env = dict(os.environ, PATH=str(tmp_path)+os.pathsep+os.environ["PATH"], DOCKER_TEST_LOG=str(log))
    subprocess.run(["bash", "-e", "-c", script], env=env, check=True, capture_output=True, text=True)
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert ["image", "inspect", "porota-test:otra-version"] in calls
    assert any(c[:5] == ["run", "--rm", "--entrypoint", "sh", "porota-test:otra-version"] for c in calls)


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
    # Deploy V2 no ejecuta el init del compose legacy. La imagen congelada
    # prepara los directorios antes de cambiar al usuario sin privilegios.
    fuente = (RAIZ / "Dockerfile").read_text(encoding="utf-8")
    preparacion = fuente[:fuente.index("USER botuser")]

    assert "useradd -m -u 1000 botuser" in preparacion
    assert "/app/data/logs" in preparacion
    assert "/app/data/backups" in preparacion
    assert "/app/sre_vector_db" in preparacion
    assert "/home/botuser/.cache" in preparacion
    assert "chown -R botuser:botuser /app /home/botuser" in preparacion


def test_dockerfile_no_escribe_bytecode_y_dirige_la_cache_al_volumen():
    fuente = (RAIZ / "Dockerfile").read_text(encoding="utf-8")

    assert "PYTHONDONTWRITEBYTECODE=1" in fuente
    assert "XDG_CACHE_HOME=/home/botuser/.cache" in fuente
    assert "MPLCONFIGDIR=/tmp/matplotlib" in fuente


def test_chroma_usa_rootfs_de_solo_lectura_con_escrituras_aisladas():
    # El runtime productivo vigente es el split de porota_mode_manager; no el
    # servicio Chroma del compose legacy. El observer y los verificadores
    # efimeros deben conservar rootfs read-only y escrituras acotadas a tmpfs.
    manager = (RAIZ / "porota_mode_manager.py").read_text(encoding="utf-8")
    deploy = (RAIZ / ".github/workflows/porota-deploy-v2-promote.yml").read_text(
        encoding="utf-8"
    )

    observer = manager[manager.index('created = run("docker", "run"'):]
    assert '"--user", "botuser", "--read-only", "--cap-drop", "ALL"' in observer
    assert '"--security-opt", "no-new-privileges:true"' in observer
    assert '"--tmpfs", "/tmp:rw,noexec,nosuid,size=32m"' in observer
    assert "docker compose" not in deploy
    assert "--user botuser --read-only --cap-drop ALL" in deploy
    assert "--security-opt no-new-privileges:true" in deploy
