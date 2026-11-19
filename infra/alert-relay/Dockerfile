FROM python:3.14.10-slim@sha256:ac25e5afbfdb4a94ff99cc24b0345719210133144481480bcba6085efa36a341

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN groupadd --gid 10001 relay \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin relay \
    && mkdir /data && chown 10001:10001 /data && chmod 0700 /data
COPY --chown=10001:10001 relay.py /app/relay.py
USER 10001:10001
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=3s --retries=3 CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2).close()"]
CMD ["python", "/app/relay.py"]