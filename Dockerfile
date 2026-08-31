# syntax=docker/dockerfile:1.7

# Safactory Job Server runtime for Linux/AMD64. The PJLab base already provides
# uv, system Python, pip, brainpp, Docker CLI, curl, git and build tools.
ARG BASE_IMAGE=registry.h.pjlab.org.cn/ailab/ml-base:22.04-pjlab-20260323
FROM ${BASE_IMAGE}

ARG HTTP_PROXY=http://httpproxy-headless.kubebrain.svc.pjlab.local:3128
ARG HTTPS_PROXY=http://httpproxy-headless.kubebrain.svc.pjlab.local:3128
ARG NO_PROXY=localhost,127.0.0.1,.pjlab.local,.pjlab.org.cn
ARG PIP_INDEX_URL=https://pypi.org/simple

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VIRTUAL_ENV=/opt/safactory-venv \
    PATH=/opt/safactory-venv/bin:${PATH} \
    SAFACTORY_INITIALIZATION_CONFIG_PATH=/app/deploy/initialization.yaml \
    SAFACTORY_AUTH_CONFIG_PATH=/app/deploy/trusted_api_key.yaml \
    SAFACTORY_HOST=0.0.0.0 \
    SAFACTORY_PORT=8000

WORKDIR /app

# Copy dependency metadata and package sources first so dependency installation
# remains cached when only datasets, runtime files or documentation change.
COPY pyproject.toml README.md ./
COPY src ./src

# The project requires Python 3.11+ while ml-base's system Python is 3.10.
# Reuse the uv binary already shipped by ml-base to create an isolated Python
# 3.12 environment and install only this project's declared dependencies.
RUN --mount=type=cache,target=/root/.cache/uv \
    export HTTP_PROXY="${HTTP_PROXY}" HTTPS_PROXY="${HTTPS_PROXY}" \
        http_proxy="${HTTP_PROXY}" https_proxy="${HTTPS_PROXY}" \
        NO_PROXY="${NO_PROXY}" no_proxy="${NO_PROXY}" \
        UV_HTTP_TIMEOUT=300; \
    test -x /root/.local/bin/uv \
    && /root/.local/bin/uv --native-tls python install 3.12 \
    && /root/.local/bin/uv --native-tls venv \
        --python 3.12 "${VIRTUAL_ENV}"

RUN --mount=type=cache,target=/root/.cache/uv \
    export HTTP_PROXY="${HTTP_PROXY}" HTTPS_PROXY="${HTTPS_PROXY}" \
        http_proxy="${HTTP_PROXY}" https_proxy="${HTTPS_PROXY}" \
        NO_PROXY="${NO_PROXY}" no_proxy="${NO_PROXY}" \
        UV_HTTP_TIMEOUT=300; \
    /root/.local/bin/uv --native-tls pip install \
        --python "${VIRTUAL_ENV}/bin/python" \
        --index-url "${PIP_INDEX_URL}" \
        --index-strategy unsafe-best-match \
        . \
    && "${VIRTUAL_ENV}/bin/python" -c \
        "import brainpp, fastapi, pydantic, server, uvicorn, wt_sdk, yaml"

# Package the complete repository data into the image. .dockerignore keeps only
# local VCS/cache artifacts out of the build context.
COPY . /app

RUN for required_file in \
        /app/.env.real \
        /app/deploy/initialization.yaml \
        /app/deploy/trusted_api_key.yaml \
        /app/examples/real/ranges.yaml; do \
        if [ ! -f "${required_file}" ]; then \
            echo >&2 "ERROR: required file is missing from the image: ${required_file}"; \
            exit 1; \
        fi; \
    done \
    && if [ ! -d /app/env ]; then \
        echo >&2 "ERROR: required directory is missing from the image: /app/env"; \
        exit 1; \
    fi \
    && chmod 600 /app/.env.real \
    && mkdir -p /app/runtime /app/results /app/logs \
    && mv "${VIRTUAL_ENV}/bin/safactory-job-server" \
        "${VIRTUAL_ENV}/bin/safactory-job-server.real" \
    && printf '%s\n' \
        '#!/bin/bash' \
        'set -Eeo pipefail' \
        'set -a' \
        'source /app/.env.real' \
        'set +a' \
        'exec /opt/safactory-venv/bin/safactory-job-server.real "$@"' \
        > "${VIRTUAL_ENV}/bin/safactory-job-server" \
    && chmod 755 "${VIRTUAL_ENV}/bin/safactory-job-server"

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import os, socket; socket.create_connection(('127.0.0.1', int(os.getenv('SAFACTORY_PORT', '8000'))), timeout=2).close()"]

# Docker uses this entrypoint normally. PJLab worker-init may bypass ENTRYPOINT
# and execute CMD directly; the safactory-job-server wrapper above also sources
# .env.real, so both launch paths receive the same configuration.
ENTRYPOINT ["/bin/bash", "-c", "set -Eeo pipefail; set -a; source /app/.env.real; set +a; exec \"$@\"", "--"]
CMD ["safactory-job-server"]
