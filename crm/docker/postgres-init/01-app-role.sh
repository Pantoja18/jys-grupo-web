#!/bin/sh
# Se ejecuta UNA sola vez, cuando el volumen de PostgreSQL está vacío (docker-entrypoint-initdb.d).
# Crea el rol de la aplicación "jys_app" (sin superusuario, sin crear BD ni roles). Las tablas y los
# permisos sobre ellas los crea la migración de Alembic, que corre como el dueño (POSTGRES_USER).
set -eu

if [ -z "${POSTGRES_APP_PASSWORD:-}" ]; then
  echo "Falta POSTGRES_APP_PASSWORD en el .env: no se puede crear el rol jys_app." >&2
  exit 1
fi

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
     -v app_password="$POSTGRES_APP_PASSWORD" -v db_name="$POSTGRES_DB" <<'EOSQL'
SELECT format('CREATE ROLE jys_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD %L', :'app_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'jys_app') \gexec
REVOKE ALL ON DATABASE :"db_name" FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE :"db_name" TO jys_app;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
EOSQL

echo "Rol jys_app creado."
