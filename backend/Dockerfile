FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_PROJECT_ENVIRONMENT=/opt/venv
RUN pip install --no-cache-dir uv==0.11.33
WORKDIR /app/backend

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY alembic.ini ./
COPY app ./app
RUN uv sync --frozen --no-dev

ENV PATH="/opt/venv/bin:$PATH"
RUN useradd --create-home --uid 10001 ao && mkdir -p /app/data && chown -R ao /app
USER ao
EXPOSE 8000
# One worker: rate limits, lockouts, the token denylist and ML jobs are in-process (SECURITY.md §3.1).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers"]
