FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

RUN pip install --no-cache-dir uv==0.11.29

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY holdout ./holdout
COPY tests/fixtures/triage ./tests/fixtures/triage

RUN uv sync --frozen --no-dev

EXPOSE 8000
CMD ["sh", "-c", "exec uv run sentinel serve --host 0.0.0.0 --port ${PORT:-8000}"]
