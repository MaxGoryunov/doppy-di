FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir --only-binary=:all: uv && useradd --create-home appuser

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY tests ./tests
RUN uv sync --frozen --extra dev && chown -R appuser:appuser /app

USER appuser

CMD ["bash", "-lc", "uv run pytest"]
