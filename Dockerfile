FROM python:3.13-slim AS builder
RUN pip install --no-cache-dir uv
WORKDIR /app
COPY pyproject.toml ./
RUN uv pip install --system --no-cache .

FROM python:3.13-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN groupadd --system app && useradd --system --gid app --home /app app
WORKDIR /app
COPY --from=builder /usr/local /usr/local
COPY . .
RUN mkdir -p /app/media /app/staticfiles && chown -R app:app /app
USER app
RUN DJANGO_SETTINGS_MODULE=config.settings.local python manage.py collectstatic --noinput
EXPOSE 8000
CMD ["gunicorn", "config.asgi:application", "-k", "uvicorn.workers.UvicornWorker", "--bind", "0.0.0.0:8000", "--workers", "2", "--access-logfile", "-"]
