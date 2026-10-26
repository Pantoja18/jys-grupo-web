# Imagen de crm-api (y de crm-tools: migraciones, scripts y pruebas). Se construye desde whatsapp-bot/docker-compose.yml.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY pyproject.toml ./
COPY app ./app
COPY scripts ./scripts
# Incluye pytest/httpx para poder correr las pruebas en el contenedor (docker compose run --rm crm-tools pytest).
RUN pip install ".[dev]"

COPY alembic.ini ./
COPY migrations ./migrations
COPY tests ./tests

# Usuario sin privilegios. crm-api corre como "crm" (uid 10001); crm-tools corre con el uid/gid de n8n (1000:1000,
# ver docker-compose.yml) para leer datos/citas.jsonl.enc, que n8n crea con permisos 600. /app queda de solo lectura
# para ese uid, así que pytest no guarda caché y HOME apunta a /tmp.
RUN useradd --system --uid 10001 --no-create-home crm && chown -R crm /app
ENV HOME=/tmp     PYTEST_ADDOPTS="-p no:cacheprovider"
USER crm

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-server-header"]
