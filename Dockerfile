FROM python:3.14.10-slim-bookworm@sha256:3fe1a0b6c2d6b2ebb572906cdabeca75aeb558c0c17db5ffefbcf8094871c32b AS builder
WORKDIR /build
COPY requirements.txt .
RUN python -m venv /opt/venv \
    && /opt/venv/bin/python -m pip install --no-cache-dir --only-binary=:all: -r requirements.txt

FROM python:3.14.10-slim-bookworm@sha256:3fe1a0b6c2d6b2ebb572906cdabeca75aeb558c0c17db5ffefbcf8094871c32b AS runtime
ARG VERSION=dev
ARG GIT_SHA=unknown
ARG BUILD_DATE=unknown
ENV VERSION=${VERSION} \
    GIT_SHA=${GIT_SHA} \
    BUILD_DATE=${BUILD_DATE} \
    PORT=8000 \
    DATA_DIR=/data \
    SQLITE_BUSY_TIMEOUT_MS=5000 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:${PATH}"
LABEL org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${GIT_SHA}" \
      org.opencontainers.image.created="${BUILD_DATE}"
RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin app \
    && mkdir /data \
    && chown 10001:10001 /data
WORKDIR /srv
COPY --from=builder /opt/venv /opt/venv
COPY app/ /srv/app/
USER 10001:10001
EXPOSE 8000
STOPSIGNAL SIGTERM
CMD ["gunicorn", "--config", "/srv/app/gunicorn.conf.py", "app:create_app()"]