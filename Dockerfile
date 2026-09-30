FROM python:3.12-slim # nosonar docker:S6471 slim image, non-root USER appuser below

WORKDIR /app

# nosonar docker:S8541 pip only installs uv binary, --only-binary set
# nosonar docker:S8544 uv.lock pins all Python deps, --frozen used below
RUN pip install --no-cache-dir --only-binary=:all: uv && useradd --create-home appuser

# nosonar docker:S6470 only build files copied, no secrets in build context
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY tests ./tests
# nosonar docker:S8541,S8544 lockfile+frozen, no setup.py
RUN uv sync --frozen --extra dev && chown -R appuser:appuser /app

USER appuser

CMD ["bash", "-lc", "uv run pytest"]
