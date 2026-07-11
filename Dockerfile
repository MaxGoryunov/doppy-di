FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY . /app/
RUN uv sync --extra dev

CMD ["bash", "-lc", "uv run pytest"]
