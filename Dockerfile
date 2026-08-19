FROM mcr.microsoft.com/devcontainers/python:1-3.11-bookworm

ARG TALIB_VERSION=0.7.1

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        gcc \
        make \
        wget \
        ca-certificates \
        pkg-config \
        autoconf \
        automake \
        libtool \
        python3-dev \
        libopenblas-dev \
        liblapack-dev \
        libffi-dev \
        libssl-dev \
        wkhtmltopdf \
    && rm -rf /var/lib/apt/lists/*

# Build and install the TA-Lib C library before installing the Python wrapper.
RUN cd /tmp \
    && wget -q https://github.com/TA-Lib/ta-lib/releases/download/v${TALIB_VERSION}/ta-lib-${TALIB_VERSION}.tar.gz \
    && tar -xzf ta-lib-${TALIB_VERSION}.tar.gz \
    && cd ta-lib-${TALIB_VERSION} \
    && ./configure --prefix=/usr/local \
    && make -j"$(nproc)" \
    && make install \
    && ldconfig \
    && cd / \
    && rm -rf /tmp/ta-lib-${TALIB_VERSION} /tmp/ta-lib-${TALIB_VERSION}.tar.gz

WORKDIR /workspaces/Porota-trading

COPY requirements.txt /tmp/requirements.txt
RUN python -m pip install --upgrade pip setuptools wheel \
    && python -m pip install --no-cache-dir -r /tmp/requirements.txt

COPY .devcontainer/post-create.sh /usr/local/bin/porota-post-create
RUN chmod +x /usr/local/bin/porota-post-create

CMD ["sleep", "infinity"]
