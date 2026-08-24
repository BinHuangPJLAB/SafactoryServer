FROM python:3.12-slim AS runtime-base

WORKDIR /app

ARG PIP_INDEX_URL=https://pypi.org/simple

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=10 \
    SAFACTORY_MODE=mock \
    SAFACTORY_HOST=0.0.0.0 \
    SAFACTORY_PORT=8000

COPY pyproject.toml README.md ./
COPY src ./src
COPY env ./env
COPY examples/real ./config/real
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --index-url "${PIP_INDEX_URL}" .

RUN groupadd --system safactory \
    && useradd --system --gid safactory --no-create-home safactory \
    && chown -R safactory:safactory /app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import os, socket; socket.create_connection(('127.0.0.1', int(os.getenv('SAFACTORY_PORT', '8000'))), timeout=2).close()"]

CMD ["safactory-job-server"]

# Optional least-privilege target for environments that do not run worker-init.
# Build with: docker build --target runtime-nonroot .
FROM runtime-base AS runtime-nonroot

USER safactory

# Default deployment target. The platform's worker-init writes SSH utilities and
# runtime files under /bin, /usr, /etc, and /dev before starting the application.
FROM runtime-base AS runtime-root

USER root
