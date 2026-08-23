# ============================================================================
# Dockerfile - Porota Trading v16.2
# ============================================================================
FROM python:3.11-slim

# No escribir bytecode en la capa del contenedor. Las caches descargables van
# a un volumen persistente y los temporales a tmpfs desde Docker Compose.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    XDG_CACHE_HOME=/home/botuser/.cache \
    MPLCONFIGDIR=/tmp/matplotlib

# Dependencias de sistema minimas. Se elimino `curl`: el healthcheck ahora usa
# el propio interprete de Python, que siempre esta presente. Un healthcheck que
# depende de un binario que puede no estar instalado es un healthcheck que
# falla en silencio y deja el contenedor marcado como unhealthy para siempre.
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copiar solo requirements primero (cache de capas: si cambia el codigo pero no
# las dependencias, no se reinstala nada).
COPY requirements.txt .

# UNA SOLA fuente de dependencias. Ya no hay `pip install` suelto detras de
# esta linea: todo lo que entra a la imagen esta declarado y fijado en
# requirements.txt.
RUN pip install --no-cache-dir -r requirements.txt

# El .dockerignore (nuevo en v16.2) impide que este COPY meta archivos de
# entorno, la base de datos o el indice vectorial dentro de la imagen.
COPY . .

# El bot corre como usuario sin privilegios. Los directorios persistentes se
# crean y se ceden ANTES de cambiar de usuario: si se montan volumenes sobre
# ellos, el UID 1000 ya es el duenio y puede escribir.
RUN useradd -m -u 1000 botuser \
    && mkdir -p /app/data /app/data/logs /app/data/backups /app/data/proposals /app/sre_vector_db \
       /home/botuser/.cache /home/botuser/.config \
    && chown -R botuser:botuser /app /home/botuser
USER botuser

# Healthcheck sin curl y contra el puerto real configurado.
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import os,urllib.request,sys; p=os.getenv('DASHBOARD_PORT','8000'); sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{p}/health', timeout=5).status==200 else 1)" || exit 1

CMD ["python", "entrypoint.py"]
