# Separate from the app-owned runtime Dockerfile. No host workspace bind mount.
FROM python:3.14.10-slim-bookworm@sha256:3fe1a0b6c2d6b2ebb572906cdabeca75aeb558c0c17db5ffefbcf8094871c32b AS test
WORKDIR /workspace
COPY requirements.txt requirements-dev.txt ./
RUN python -m pip install --no-cache-dir --only-binary=:all: -r requirements-dev.txt
COPY pyproject.toml ./
COPY app/ app/
COPY tests/ tests/
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
# Running tests, rather than RUN pytest, preserves reports even when tests fail.
CMD ["python", "-m", "pytest"]