# CRM de JS CONSULTORES Y ABOGADOS

API interna y base de datos del sistema de gestión del estudio. Diseño completo y plan por fases: [`ARQUITECTURA.md`](ARQUITECTURA.md).

**Estado: Fase 1 (infraestructura y base de datos).** Todavía no hay panel web (Fase 2) ni integración con Valeria/n8n (Fase 3): el flujo de n8n sigue guardando las citas en `whatsapp-bot/datos/citas.jsonl.enc`, y `scripts/importar_citas.py` las trae al CRM.

## Qué incluye

| Pieza | Detalle |
|---|---|
| `postgres` | PostgreSQL 16 (`jys-postgres`), volumen `jys_pg_data`, **sin puertos publicados**: solo lo alcanzan `crm-api` y `crm-tools` por la red interna `jys`. |
| `crm-api` | FastAPI (`jys-crm`), publicado **solo en `127.0.0.1:8000`** (no lo ven otros equipos ni pasa por ngrok). Por ahora expone `GET /health`. |
| `crm-tools` | Misma imagen, perfil `tools` (no queda corriendo): migraciones, `create_admin`, `importar_citas` y pruebas. Es el único servicio con la clave del dueño de la BD y con `CITAS_FILE_KEY`. |
| Modelo de datos | `roles`, `users`, `contacts`, `consents`, `leads`, `appointments`, `audit_log`, `imported_citas` (migración `migrations/versions/0001_*`). |

Estructura:
```
crm/
├── app/
│   ├── main.py, config.py, db.py
│   ├── api/health.py            # GET /health
│   ├── models/                  # SQLAlchemy 2 (base.py: tipo Encrypted, key_version)
│   ├── security/crypto.py       # AES-256-GCM con versión de clave + phone_hash (HMAC-SHA256)
│   ├── security/passwords.py    # argon2id
│   ├── security/audit.py        # inserción en audit_log
│   └── services/citas_import.py # importación de citas.jsonl.enc
├── migrations/                  # Alembic
├── scripts/create_admin.py, scripts/importar_citas.py
├── docker/postgres-init/01-app-role.sh   # crea el rol jys_app (primer arranque del volumen)
├── tests/                       # pytest
├── Dockerfile, pyproject.toml, alembic.ini
```

## Seguridad (Ley N.° 29733)

- **Dos roles de base de datos.** `POSTGRES_USER` (por defecto `jys_owner`) es el dueño de las tablas y solo lo usa `crm-tools` para migrar y probar. La aplicación se conecta como **`jys_app`**, sin superusuario y sin permiso para crear tablas, con `UPDATE`/`DELETE` solo donde hace falta.
- **`audit_log` solo admite inserciones**, por dos vías:
  - `jys_app` solo tiene `SELECT` e `INSERT` sobre esa tabla y no puede desactivar el trigger, porque no es su dueño.
  - Un trigger rechaza `UPDATE`, `DELETE` y `TRUNCATE` para cualquier rol, incluido el dueño.
- **`consents`** es la prueba del consentimiento: la aplicación solo puede insertar, y un contacto con consentimientos no se puede borrar (`ON DELETE RESTRICT`).
- **Campos cifrados en la aplicación** con AES-256-GCM (`bytea`):
  - Campos: nombre, teléfono, correo y nombre de perfil del contacto, resumen de la consulta, notas de la cita y secreto TOTP.
  - Formato de cada valor: `versión de clave (2 bytes) | nonce (12) | cifrado + etiqueta`.
  - El AAD liga cada valor a su columna y a la versión de la clave.
  - Cada fila guarda `key_version`.
- **`phone_hash`** = HMAC-SHA256(`CRM_HMAC_KEY`, teléfono normalizado): permite buscar un contacto por teléfono sin descifrar.
- **Sin datos personales en logs ni respuestas:**
  - Las consultas SQL ocultan sus parámetros (`hide_parameters`).
  - `/health` no da detalles del error.
  - Los scripts solo muestran conteos.
  - `audit_log.details` lleva solo metadatos.
- **Cabeceras de seguridad:** `X-Frame-Options: DENY`, CSP `default-src 'none'`, `nosniff`, `no-referrer`. `/docs` está apagado (solo con `CRM_ENV=dev`).
- **Variables mínimas por servicio:** `crm-api` y `crm-tools` reciben solo las variables que necesitan, no el `.env` completo.

> ⚠️ **Si se pierde `CRM_DATA_KEY_V1` (o la versión vigente), los datos cifrados son irrecuperables.** Guarde una copia del `.env` en un gestor de contraseñas (por ejemplo, como adjunto o nota segura) y active el cifrado de disco del equipo (BitLocker / Cifrado del dispositivo).

## 1. Variables del `.env` (una sola vez)

Están documentadas en `whatsapp-bot/.env.example` (sección *CRM*). Este bloque de PowerShell las genera al azar y las **agrega al final de `whatsapp-bot\.env` sin mostrarlas en pantalla**. Si ya existen variables del CRM en el archivo, no hace nada.

```powershell
cd whatsapp-bot
function New-RandomB64([int]$n = 32) { $b = New-Object byte[] $n; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b); [Convert]::ToBase64String($b) }
function New-RandomHex([int]$n = 32) { $b = New-Object byte[] $n; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b); -join ($b | ForEach-Object { $_.ToString('x2') }) }
if (Select-String -Path .env -Pattern '^(POSTGRES_|CRM_|RETENTION_LEADS_MONTHS)' -Quiet) {
  Write-Host 'El .env ya tiene variables del CRM: revíselas a mano antes de agregar otras.'
} else {
  Add-Content -Path .env -Encoding ascii -Value @(
    '',
    '# ---------- CRM (Fase 1) ----------',
    'POSTGRES_USER=jys_owner',
    "POSTGRES_PASSWORD=$(New-RandomHex)",
    "POSTGRES_APP_PASSWORD=$(New-RandomHex)",
    "CRM_DATA_KEY_V1=$(New-RandomB64)",
    'CRM_ACTIVE_KEY_VERSION=1',
    "CRM_HMAC_KEY=$(New-RandomB64)",
    "CRM_SECRET_KEY=$(New-RandomB64)",
    "CRM_BOT_API_KEY=$(New-RandomHex)",
    'CRM_BASE_URL=http://crm-api:8000',
    'RETENTION_LEADS_MONTHS=12'
  )
  Write-Host 'Variables del CRM agregadas al .env (no se muestran).'
}
# Comprobar sin ver los valores: muestra solo el nombre y la longitud de cada una.
Select-String -Path .env -Pattern '^(POSTGRES_\w+|CRM_\w+|RETENTION_LEADS_MONTHS|POLICY_VERSION|CITAS_FILE_KEY)=(.*)$' | ForEach-Object { '{0}: {1} caracteres' -f $_.Matches[0].Groups[1].Value, $_.Matches[0].Groups[2].Value.Length }
```

- **Formato de las claves:**
  - Contraseñas de PostgreSQL y `CRM_BOT_API_KEY`: 64 caracteres hex.
  - `CRM_DATA_KEY_V1`, `CRM_HMAC_KEY` y `CRM_SECRET_KEY`: 32 bytes en base64 (44 caracteres).
- **`DATABASE_URL`:** no hace falta en Docker, porque `docker-compose.yml` arma la conexión con `POSTGRES_APP_PASSWORD`. Úsela solo para correr la API fuera de Docker.
- **Variables que ya existían:** `POLICY_VERSION` y `CITAS_FILE_KEY` ya estaban en el `.env` (Fase 0); el CRM usa las mismas.
- **Cuándo cuentan las contraseñas de PostgreSQL:** `POSTGRES_PASSWORD` y `POSTGRES_APP_PASSWORD` se aplican **solo la primera vez** que se crea el volumen `jys_pg_data`. Para cambiar la de `jys_app` más adelante:
  1. `docker compose exec -it postgres psql -U jys_owner -d jys` → `\password jys_app` (no queda en el historial).
  2. Actualice el `.env`.
  3. `docker compose up -d --force-recreate crm-api`.

## 2. Levantar, migrar y comprobar (desde `whatsapp-bot\`)

```powershell
docker compose config --quiet                          # valida el archivo (sin --quiet mostraría los secretos)
docker compose up -d --build                           # n8n + postgres + crm-api
docker compose ps                                      # jys-postgres "healthy"; jys-crm y js-consultores-n8n "running"
docker compose run --rm crm-tools alembic upgrade head # crea las tablas (una vez y tras cada migración nueva)
curl.exe -s http://127.0.0.1:8000/health               # {"status":"ok","database":"ok"}
curl.exe -s http://localhost:5678/healthz              # n8n sigue igual: {"status":"ok"}
docker port jys-postgres                               # vacío: PostgreSQL no está publicado
```

## 3. Usuario administrador

```powershell
docker compose run --rm crm-tools python -m scripts.create_admin
```
Pide el correo (que será el usuario), el nombre y la contraseña, esta última dos veces y sin mostrarla. La contraseña debe tener al menos 12 caracteres. Se guarda con argon2id y la creación queda en `audit_log`.

## 4. Importar las citas de n8n

```powershell
docker compose run --rm crm-tools python -m scripts.importar_citas
```
- **Qué lee:** `whatsapp-bot\datos\citas.jsonl.enc`, montado en solo lectura, con `CITAS_FILE_KEY`.
- **Qué crea:** el contacto, el consentimiento (`whatsapp_import`, con la versión de la política), la consulta (`cita_solicitada`) y la solicitud de cita (`solicitada`, origen `valeria`). Todo se guarda cifrado.
- **Se puede repetir:** las líneas ya importadas se saltan (`imported_citas`).
- **Varias versiones de una misma solicitud:** si son del mismo número y están dentro de 24 h, se unen en una sola cita.
- **Sin consentimiento otorgado:** no se importan.
- **Qué muestra:** solo conteos (`nuevas`, `actualizadas`, `repetidas`, `sin consentimiento`, `invalidas`).

## 5. Pruebas

```powershell
docker compose run --rm crm-tools pytest
```
- **Base de datos de pruebas:** usan una BD propia, `jys_test`, que se borra y se vuelve a crear en cada ejecución, y un rol `jys_test_app`. **Nunca tocan la BD `jys`.**
- **Qué cubren:**
  - Cifrado y descifrado, nonces distintos, rotación de clave y `phone_hash`.
  - argon2id.
  - Que `audit_log` rechace `UPDATE`, `DELETE` y `TRUNCATE`, tanto del rol de la app como del dueño.
  - Datos cifrados en la BD.
  - Que la migración coincida con los modelos.
  - `/health`.
  - Importación sin duplicados y `create_admin`.
- **Sin Docker:** con un PostgreSQL de pruebas, `$env:TEST_DATABASE_URL='postgresql+psycopg://usuario:clave@127.0.0.1:5432/jys_test'; python -m pytest` desde `crm\`. Sin BD de pruebas, esas pruebas se omiten.

## Rotar la clave de cifrado

1. Genere `CRM_DATA_KEY_V2` y agréguela al `.env`, sin borrar la V1.
2. Ponga `CRM_ACTIVE_KEY_VERSION=2` y ejecute `docker compose up -d --force-recreate crm-api`.
3. A partir de ahí:
   - Lo nuevo se cifra con la V2 y lo viejo se sigue leyendo con la V1.
   - Cada fila que se modifica se vuelve a cifrar entera con la V2 (`key_version` lo indica).
   - Un script de re-cifrado masivo queda para la Fase 5.
4. Solo cuando ninguna fila tenga `key_version = 1` se puede retirar la V1.

## Pendiente para fases siguientes

- **Fase 2:** panel, login con sesiones y bloqueo, TOTP y permisos por rol.
- **Fase 3:**
  - API `/api/bot/*` (`CRM_BOT_API_KEY`) e integración con n8n.
  - Tablas `interactions`, `re_projects` (`leads.project_id`) y `settings`.
- **Fase 5:** `backup.ps1`, `purge_expired.py` (retención), ARCO y re-cifrado masivo.
