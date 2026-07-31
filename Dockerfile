FROM ghcr.io/astral-sh/uv:0.12.0-python3.12-trixie-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_NO_DEV=1

WORKDIR /app

COPY pyproject.toml ./
RUN uv sync --no-dev

# Copy the full project so an optional root CSV is available to the API.
COPY . .

EXPOSE 8000

CMD ["uv", "run", "--locked", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
