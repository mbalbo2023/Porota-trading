# ============================================================================
# Dockerfile - Porota Trading v16.2
# ============================================================================
FROM python:3.11-slim@sha256:da047cb8f9d1d98e5c070f5300ba9f7274e33b8fc0e5be5ed88740aed1b95ba9

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
COPY requirements.txt requirements.lock.txt ./

# requirements.txt conserva la intención humana/top-level. requirements.lock.txt
# congela el entorno exacto validado por Predeploy V2; runtime instala sólo el lock.
RUN pip install --no-cache-dir -r requirements.lock.txt

# El .dockerignore (nuevo en v16.2) impide que este COPY meta archivos de
# entorno, la base de datos o el indice vectorial dentro de la imagen.
COPY . .

# Reassert every dashboard module verified by the RC6 deploy.  `COPY . .` is a
# convenience layer, never the provenance authority for the live dashboard.
# Each of these sources is copied from the immutable candidate context.
COPY bg_paper_dashboard.py /app/bg_paper_dashboard.py
COPY zz_wave8_dashboard_live_rc6.py /app/zz_wave8_dashboard_live_rc6.py
COPY da_dashboard_ux_hf6.py /app/da_dashboard_ux_hf6.py
COPY rc6_annual_instrument_analysis.py /app/rc6_annual_instrument_analysis.py
COPY rc6_on_validation.py /app/rc6_on_validation.py
COPY bd_ppi_readonly_guard.py /app/bd_ppi_readonly_guard.py
COPY c_ppi_client.py /app/c_ppi_client.py
COPY cr_pending_settlement_diagnostics_hf6.py /app/cr_pending_settlement_diagnostics_hf6.py
COPY eq_dashboard_table_layout_rc6.py /app/eq_dashboard_table_layout_rc6.py
COPY rc6_dashboard_responsive_ux.py /app/rc6_dashboard_responsive_ux.py
COPY rc6_family_readiness.py /app/rc6_family_readiness.py
COPY o_dashboard.py /app/o_dashboard.py
# Reassert the intraday worker explicitly; the observer imports it as a child process.
COPY cf_intraday_scalping.py /app/cf_intraday_scalping.py

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
