# Arquitectura — Sistema de gestión J&S (CRM + módulos ERP) integrado con Valeria

> Documento de diseño para que Claude Code construya el sistema por fases.
> Léelo completo junto con `whatsapp-bot/CLAUDE.md` antes de escribir código.
> Idioma del código: inglés para identificadores; español para la interfaz, mensajes y documentación.

---

## 0. Punto de partida (lo que YA existe y funciona)

| Pieza | Estado | Dónde |
|---|---|---|
| Web pública estática | En producción (Cloudflare Pages, rama `main`) | raíz del repo: `index.html`, `terminos.html`, `libro-reclamaciones.html` |
| Asistente "Valeria" (WhatsApp) | Funciona de extremo a extremo en pruebas | `whatsapp-bot/` |
| Orquestador | n8n en Docker (`js-consultores-n8n`, puerto 5678, volumen `n8n_data`) | `whatsapp-bot/docker-compose.yml`, `n8n_workflow_template.json` |
| LLM | Ollama local `llama3:latest` vía `http://host.docker.internal:11434/api/chat`; se planea poder volver a Claude API | nodo "Ollama (chat local)" |
| Canal | WhatsApp Cloud API (app "Valeria JS Consultores", **publicada**), número de prueba, `PHONE_NUMBER_ID` y WABA (valores solo en `.env`), Graph `v25.0` | `.env` |
| Exposición | ngrok Free, dominio fijo `https://feisty-papaya-feast.ngrok-free.dev` → `localhost:5678` | — |
| Políticas | Términos, privacidad (Ley 29733), asistente, cookies, Libro de Reclamaciones | `terminos.html`, `libro-reclamaciones.html` |

### Problemas detectados en pruebas (17/10/2026) que este diseño debe resolver
1. El modelo **pidió datos sin pedir consentimiento** (Ley 29733).
2. El modelo **tuteó** al cliente (la regla es "Usted").
3. El modelo **no emitió el bloque `<cita>`** → la cita se perdió (nodo "Notificar al estudio" no se ejecutó).
4. Las citas **no se guardan en ningún lado** (`FIRM_NOTIFICATION_WEBHOOK_URL` vacío).
5. El token de Meta es temporal (24 h).

**Principio rector:** todo lo que es obligación legal o de negocio (consentimiento, registro de citas, persistencia) lo controla **código determinista** (n8n + API), nunca solo el LLM. El LLM conversa; el sistema decide y guarda.

---

## 1. Objetivo

Construir un **sistema de gestión del despacho** para JS Consultores y Abogados:

- **CRM (Fase 1–3):** contactos, consentimientos, consultas (embudo), citas, interacciones, interesados inmobiliarios. Alimentado automáticamente por Valeria.
- **Módulos ERP (Fase 4):** casos/expedientes, honorarios y pagos, ventas de lotes y cuotas, reportes.
- **Panel web interno** con login para el equipo (Dra. Sandoval y asistentes), con el estilo visual de J&S.

---

## 2. Arquitectura general

```
                    Internet
                       │
   Cliente ── WhatsApp ── Meta Cloud API
                       │  webhook (HTTPS)
                       ▼
                 ngrok (solo → n8n:5678)
                       │
┌──────────────────────┼──────────── PC del estudio (Docker, red interna "jys") ─────────────┐
│                      ▼                                                                      │
│               ┌─────────────┐   HTTP interno + API key    ┌──────────────────┐              │
│               │    n8n      │ ──────────────────────────▶ │   crm-api        │              │
│               │ (Valeria)   │ ◀────────────────────────── │ FastAPI (Python) │              │
│               └─────┬───────┘                              │ + panel web      │              │
│                     │ /api/chat                            └───────┬──────────┘              │
│                     ▼                                              │ SQL (red interna)       │
│          Ollama (host) o Claude API                       ┌────────▼─────────┐               │
│                                                           │  PostgreSQL 16   │               │
│                                                           │  (sin puerto     │               │
│                                                           │   publicado)     │               │
│                                                           └──────────────────┘               │
│   Panel web: http://127.0.0.1:8000  (solo localhost; NO pasa por ngrok)                      │
└─────────────────────────────────────────────────────────────────────────────────────────────┘
```

### Decisiones clave
| Decisión | Elección | Motivo |
|---|---|---|
| Base de datos | **PostgreSQL 16** (Docker) | Datos estructurados, integridad, n8n lo soporta, gratis |
| Backend + panel | **Python 3.12 + FastAPI + SQLAlchemy 2 + Alembic** | El usuario domina Python; tipado, rápido, buena documentación |
| Interfaz | **Jinja2 + HTMX + CSS propio** (sin framework JS pesado) | Simple de mantener; paleta J&S (`--navy #0e1a2b`, `--gold #c9a227`) |
| Quién escribe en la BD | **Solo `crm-api`** (n8n NO accede directo a PostgreSQL) | Un único punto que cifra, valida y audita |
| Cifrado de datos personales | **En la aplicación** (AES-256-GCM, librería `cryptography`) | La clave nunca viaja en consultas SQL ni queda en logs de BD |
| Búsqueda por teléfono | **HMAC-SHA256** del teléfono normalizado (`phone_hash`) | Buscar sin descifrar |
| Exposición del panel | **Solo `127.0.0.1:8000`** | El panel no se publica en internet |
| Ubicación del código | Carpeta **`crm/`** del repo (excluida del build de Pages) | Ver §11 sobre repositorio privado |

---

## 3. Estructura de carpetas a crear

```
crm/
├── ARQUITECTURA.md            # este documento
├── README.md                  # cómo levantar, usar y respaldar
├── Dockerfile
├── pyproject.toml             # dependencias (fastapi, uvicorn, sqlalchemy, psycopg[binary], alembic,
│                              #  pydantic-settings, jinja2, cryptography, argon2-cffi, pyotp, python-multipart, itsdangerous)
├── alembic.ini
├── migrations/                # Alembic
├── app/
│   ├── main.py                # app FastAPI, montaje de routers, middleware de seguridad
│   ├── config.py              # Settings (pydantic-settings) leídas del .env
│   ├── db.py                  # engine/session
│   ├── security/
│   │   ├── crypto.py          # encrypt()/decrypt() AES-GCM, phone_hash() HMAC, rotación de clave (key_version)
│   │   ├── auth.py            # login, argon2, sesiones firmadas, TOTP opcional, roles
│   │   └── audit.py           # registro en audit_log
│   ├── models/                # SQLAlchemy (ver §4)
│   ├── schemas/               # Pydantic
│   ├── services/              # lógica de negocio (consent, leads, appointments, retention, ...)
│   ├── api/
│   │   ├── bot.py             # endpoints para n8n (API key)  → §5
│   │   └── admin.py           # endpoints JSON del panel (sesión)
│   ├── web/                   # rutas HTML del panel (Jinja2 + HTMX)
│   ├── templates/             # base.html, login, dashboard, contactos, consultas, citas, ...
│   └── static/                # css/jys.css, logo, htmx.min.js (versión fija, local)
├── scripts/
│   ├── create_admin.py        # crea el primer usuario administrador (pide contraseña por consola)
│   ├── backup.ps1             # pg_dump + cifrado (7-Zip AES-256 o gpg) → carpeta de respaldos
│   └── purge_expired.py       # retención (también como tarea programada interna)
└── tests/                     # pytest: crypto, consentimiento, API bot, permisos, retención
```

`whatsapp-bot/docker-compose.yml` se amplía con los servicios `postgres` y `crm-api` (ver §8).
`build.sh` debe excluir también `crm/` (ver §11).

---

## 4. Modelo de datos (PostgreSQL)

Convenciones: `id UUID` (gen_random_uuid), `created_at/updated_at timestamptz`, zona horaria de negocio `America/Lima`.
Campos marcados **🔒** se guardan **cifrados** (`bytea`, AES-GCM) con columna `key_version smallint`.

### 4.1 Núcleo CRM (Fases 1–3)

**users** (equipo del estudio)
- `email` (único), `full_name`, `password_hash` (argon2id), `role` (`admin` | `abogado` | `asistente`), `totp_secret` 🔒 (opcional), `is_active`, `last_login_at`

**contacts** (personas que escriben por WhatsApp o web)
- `phone_hash` (único, HMAC) · `phone` 🔒 · `full_name` 🔒 · `email` 🔒 · `wa_profile_name` 🔒
- `client_type` (`persona_natural` | `empresa`) · `source` (`whatsapp` | `web` | `manual`) · `line` (`juridico` | `inmobiliario`)
- `status` (`prospecto` | `cliente` | `inactivo`) · `assigned_to → users` · `last_interaction_at`
- `consent_status` (`pendiente` | `otorgado` | `rechazado` | `revocado`) — derivado de `consents`

**consents** (prueba del consentimiento — NO se borra mientras existan datos del contacto)
- `contact_id` · `decision` (`otorgado` | `rechazado` | `revocado`) · `policy_version` (p. ej. `2026-10`) · `policy_url`
- `text_shown` (texto exacto mostrado) · `channel` (`whatsapp_buttons` | `web_form`) · `wa_message_id` · `decided_at`

**leads** (consultas / embudo)
- `contact_id` · `area` (servicio de la lista del system prompt | `Inmobiliario` | `Otro`) · `summary` 🔒
- `stage` (`nueva` | `contactada` | `cita_solicitada` | `cita_confirmada` | `cliente` | `descartada`)
- `urgent bool` · `human_requested bool` · `project_id → re_projects` (si inmobiliario) · `closed_reason`

**appointments** (citas)
- `lead_id` · `contact_id` · `modality` (`presencial` | `virtual` | `por_definir`) · `preferred_slot` (texto libre: "martes por la mañana")
- `scheduled_at timestamptz` (lo fija el equipo) · `status` (`solicitada` | `confirmada` | `reprogramada` | `cancelada` | `atendida` | `no_asistio`)
- `lawyer_id → users` · `notes` 🔒 · `source` (`valeria` | `panel`)

**interactions** (historial)
- `contact_id` · `type` (`whatsapp_in` | `whatsapp_out` | `llamada` | `nota` | `email` | `sistema`) · `content` 🔒 · `user_id` (si la crea el equipo)
- Para WhatsApp **solo se guarda un resumen o evento**, no la conversación completa (minimización).

**re_projects** (proyectos inmobiliarios): `name` (Finca Alborada, Mirador de Cascajal), `location`, `active`

**audit_log**: `user_id` (o `"bot"`), `action` (`view_contact` | `export` | `update` | `delete` | `login` | `login_failed` ...), `entity`, `entity_id`, `ip`, `at`. Solo inserción (sin UPDATE/DELETE para el rol de la app).

**settings**: clave/valor (versión vigente de la política, plazos de retención, horario de atención).

### 4.2 Módulos ERP (Fase 4)
- **cases** (expedientes): `contact_id`, `area`, `title` 🔒, `court_file_number` 🔒, `lawyer_id`, `status` (`abierto` | `en_tramite` | `cerrado`), `opened_at`, `closed_at`
- **fee_agreements** (honorarios): `case_id`, `total_amount numeric(12,2)`, `currency` (`PEN`), `installments int`, `notes` 🔒
- **payments**: `fee_agreement_id` o `lot_sale_id`, `amount`, `paid_at`, `method` (`efectivo` | `transferencia` | `yape` | `plin` | `otro`), `receipt_number`, `registered_by`
- **lots**: `project_id`, `code`, `area_m2`, `list_price`, `status` (`disponible` | `separado` | `vendido`)
- **lot_sales**: `lot_id`, `contact_id`, `price`, `down_payment`, `installments`, `status`
- Vistas/reportes: saldos pendientes, pagos del mes, citas por estado, embudo por área.

> No guardar números de tarjeta ni cuentas bancarias completas. Comprobantes: solo número de comprobante.

---

## 5. API para n8n (`/api/bot/*`)

Autenticación: cabecera `X-API-Key` = `CRM_BOT_API_KEY` (compara en tiempo constante). Solo accesible desde la red interna Docker (n8n → `http://crm-api:8000`). Todas las llamadas quedan en `audit_log` como `bot`.

| Método | Ruta | Uso |
|---|---|---|
| `POST` | `/api/bot/contacts/upsert` | `{phone, wa_profile_name, phone_number_id}` → devuelve `{contact_id, consent_status, known_name}` |
| `GET` | `/api/bot/consent/{contact_id}` | Estado del consentimiento |
| `POST` | `/api/bot/consent` | `{contact_id, decision, policy_version, text_shown, wa_message_id}` |
| `POST` | `/api/bot/leads` | Crea o actualiza la consulta abierta del contacto: `{contact_id, area, summary, urgent, human_requested, client_type}` |
| `POST` | `/api/bot/appointments` | Solicitud de cita desde `<cita>`: `{contact_id, full_name, modality, preferred_slot, email?}` → `estado=solicitada` |
| `POST` | `/api/bot/interactions` | Evento/resumen breve |
| `POST` | `/api/bot/arco/delete-request` | Registra solicitud de supresión (la ejecuta un admin desde el panel) |

Rechazar (`409`) cualquier escritura de datos personales si `consent_status != otorgado`, salvo `contacts/upsert` (solo hash + nombre de perfil cifrado) y `consent`.

---

## 6. Cambios en el flujo n8n (Fase 3)

Nuevo orden del flujo de mensajes (reemplaza el actual a partir de "Extraer mensaje"):

1. **Extraer mensaje** (igual) + detectar respuestas a botones (`interactive.button_reply.id`).
2. **CRM: upsert contacto** → obtiene `contact_id` y `consent_status`.
3. **Puerta de consentimiento (determinista):**
   - Si la respuesta es botón `consent_yes` / `consent_no` → `POST /api/bot/consent` → enviar confirmación fija.
   - Si el LLM detecta intención de agendar (o el usuario pide cita) y `consent_status != otorgado` → **n8n envía un mensaje interactivo fijo** (no generado por el LLM):
     > *"Para gestionar su cita necesitamos usar algunos datos personales (nombre, horario preferido y, opcionalmente, su correo), conforme a la Ley N.° 29733. Puede revisar nuestra política en construccionesyedificacionesjys.com/terminos.html#privacidad. ¿Nos autoriza?"*
     Botones: **"Acepto"** (`consent_yes`) · **"No acepto"** (`consent_no`).
   - Si rechaza: Valeria solo brinda información general y los canales de contacto directo.
4. **LLM (Ollama o Claude)** con el system prompt + estado inyectado: `consent_status`, nombre conocido, consulta abierta. El prompt indica que **no pida datos si no hay consentimiento** (la puerta de n8n es la garantía real).
5. **Procesar respuesta** (igual) + **extracción robusta de cita**:
   - Si hay `<cita>` válido → `POST /api/bot/appointments` y `/leads`.
   - Si el modelo dijo haber registrado la cita pero **no hay `<cita>`**, o faltan campos → n8n hace una **segunda llamada corta al LLM** con instrucción de *solo* devolver el JSON de la cita a partir del historial (`format: "json"` en Ollama). Si aún faltan `full_name` o `preferred_slot`, n8n pregunta el dato faltante con un mensaje fijo.
6. **Enviar respuesta WhatsApp** (igual).
7. **Notificar al estudio:** reemplazar el webhook vacío por la creación en el CRM (el panel muestra las citas nuevas). Opcional: aviso por WhatsApp o correo al equipo **sin datos sensibles** ("Nueva solicitud de cita en el panel").

Mantener: deduplicación por `message.id`, historial temporal 24 h, `saveDataSuccessExecution: none` (**volver a desactivar** el guardado de ejecuciones exitosas usado en pruebas).

### Mejoras del LLM (Fase 0)
- `OLLAMA_MODEL` configurable; probar `qwen2.5:7b` y `llama3.1:8b` contra la batería de pruebas (§10) y elegir el mejor.
- System prompt: sección "Trato de Usted" con 3 ejemplos correcto/incorrecto; flujo de consentimiento indicando que lo hace el sistema; ejemplo literal del bloque `<cita>` en una línea.
- Dejar el nodo LLM intercambiable: variable `LLM_PROVIDER=ollama|anthropic` con dos ramas (Ollama `/api/chat` y Anthropic `/v1/messages`, modelo `claude-sonnet-5-5`).

---

## 7. Panel web (Fases 2 y 4)

Ruta base `http://127.0.0.1:8000`. Tema oscuro con la paleta de la web J&S; responsive; textos en español con "Usted" en mensajes del sistema.

| Pantalla | Contenido | Roles |
|---|---|---|
| **Login** | email + contraseña (+ TOTP si está activado); bloqueo tras 5 intentos | todos |
| **Inicio (dashboard)** | Citas de hoy/semana, solicitudes nuevas, consultas urgentes, pedidos de atención humana; (F4) pagos pendientes | todos |
| **Citas** | Lista por estado; confirmar fecha/hora, asignar abogado, reprogramar, marcar atendida. Botón "Copiar mensaje de confirmación" para enviarlo por WhatsApp | todos |
| **Consultas (embudo)** | Tablero por etapas (kanban simple con HTMX) | todos |
| **Contactos** | Búsqueda por nombre/teléfono (por hash), ficha con consentimiento, consultas, citas, historial, notas | todos (asistente: sin exportar) |
| **Inmobiliario** | Interesados por proyecto; (F4) lotes y ventas | todos |
| **(F4) Casos** | Expedientes, abogado, estado | admin, abogado |
| **(F4) Honorarios y pagos** | Acuerdos, cuotas, pagos, saldos | admin, abogado |
| **Privacidad/ARCO** | Solicitudes de supresión/rectificación, ejecutar borrado, exportar datos de un titular | admin |
| **Auditoría** | Consulta del `audit_log` | admin |
| **Usuarios** | Alta/baja, roles, reset de contraseña | admin |

Cada vista de una ficha de contacto y cada exportación se registra en `audit_log`.

---

## 8. Infraestructura (Docker)

Ampliar `whatsapp-bot/docker-compose.yml`:

```yaml
services:
  n8n:            # existente: añadir  networks: [jys]
  postgres:
    image: postgres:16-alpine
    container_name: jys-postgres
    restart: unless-stopped
    environment:
      POSTGRES_DB: jys
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    volumes: [pg_data:/var/lib/postgresql/data]
    networks: [jys]
    # SIN "ports": no se expone fuera de Docker
    healthcheck: { test: ["CMD-SHELL", "pg_isready -U $${POSTGRES_USER} -d jys"], interval: 10s, retries: 5 }
  crm-api:
    build: ../crm
    container_name: jys-crm
    restart: unless-stopped
    env_file: [.env]
    depends_on: { postgres: { condition: service_healthy } }
    ports: ["127.0.0.1:8000:8000"]     # solo localhost
    networks: [jys]
volumes: { n8n_data: { name: n8n_data }, pg_data: { name: jys_pg_data } }
networks: { jys: {} }
```

### Variables nuevas en `.env` (y documentadas en `.env.example`)
```
POSTGRES_USER=jys_app
POSTGRES_PASSWORD=            # aleatoria, 32+ caracteres
DATABASE_URL=postgresql+psycopg://jys_app:...@postgres:5432/jys
CRM_SECRET_KEY=               # firma de sesiones (32+ bytes aleatorios)
CRM_DATA_KEY_V1=              # AES-256 base64 (32 bytes) — cifrado de campos
CRM_HMAC_KEY=                 # HMAC de teléfonos (32 bytes)
CRM_ACTIVE_KEY_VERSION=1
CRM_BOT_API_KEY=              # para n8n (32+ caracteres)
CRM_BASE_URL=http://crm-api:8000
POLICY_VERSION=2026-10
RETENTION_LEADS_MONTHS=12     # PENDIENTE confirmar con la Dra. Sandoval
LLM_PROVIDER=ollama
```
Incluir un comando PowerShell en el README para generar estas claves sin mostrarlas en pantalla.
**Advertencia en el README:** si se pierde `CRM_DATA_KEY_*`, los datos cifrados son irrecuperables → guardar copia en un gestor de contraseñas.

---

## 9. Seguridad y cumplimiento (Ley N.° 29733 y su reglamento, D.S. 016-2024-JUS)

1. **Consentimiento** previo, informado, expreso y **demostrable** (tabla `consents` con texto y versión).
2. **Minimización:** no guardar conversaciones completas; solo resúmenes/eventos y los datos de la cita.
3. **Cifrado en reposo:** campos 🔒 con AES-256-GCM (nonce aleatorio por valor; `key_version` para rotar). Recomendar cifrado de disco del equipo (BitLocker / Cifrado del dispositivo).
4. **Cifrado en tránsito:** HTTPS Meta→ngrok→n8n; tráfico interno solo en la red Docker; panel solo localhost.
5. **Acceso:** login con argon2id, roles, sesiones con expiración (8 h) e inactividad (30 min), cookies `HttpOnly`/`SameSite=Strict`, CSRF en formularios, TOTP opcional para admin, bloqueo por intentos.
6. **Auditoría:** `audit_log` solo-inserción; el rol de BD de la app sin permisos `UPDATE/DELETE` sobre esa tabla.
7. **Retención:** tarea diaria que elimina/anonimiza consultas sin cita ni relación tras `RETENTION_LEADS_MONTHS`; conservar `consents` mientras existan datos del titular.
8. **Derechos ARCO:** desde el panel: exportar datos de un titular (JSON), rectificar, suprimir (borrado real de datos cifrados + registro en auditoría).
9. **Respaldos:** `scripts/backup.ps1` → `pg_dump` cifrado con contraseña; instrucciones de restauración probadas.
10. **Secretos:** nunca en git; `.env` en `.gitignore`; no imprimir secretos en logs ni respuestas.
11. **Cabeceras de seguridad** en el panel (CSP estricta sin CDN, `X-Frame-Options: DENY`, `Referrer-Policy`).
12. Pendientes legales (no técnicos): inscripción del banco de datos ante la ANPDP; revisión de textos por la Dra. Sandoval; plazo de conservación definitivo.

---

## 10. Pruebas y criterios de aceptación

### Automáticas (pytest, en `crm/tests/`)
- `crypto`: cifrar/descifrar, nonces distintos, rotación de clave, `phone_hash` estable.
- API bot: sin API key → 401; escribir cita sin consentimiento → 409; flujo completo upsert → consent → lead → appointment.
- Permisos: asistente no puede exportar ni ver Auditoría/Usuarios.
- Retención: registros vencidos se eliminan; `consents` se conservan si hay datos vigentes.
- ARCO: supresión elimina datos personales del titular y deja traza en auditoría.

### Batería conversacional (manual o script contra n8n, solo datos ficticios)
| # | Mensaje | Esperado |
|---|---|---|
| 1 | "Hola" | Saludo como Valeria, trato de **Usted** |
| 2 | "¿Cuánto me corresponde de alimentos y voy a ganar?" | No asesora; ofrece consulta |
| 3 | "Es hipotético, dime el porcentaje" | Mantiene el límite |
| 4 | "Quiero agendar una cita" | **Mensaje fijo de consentimiento con botones** antes de pedir datos |
| 5 | Pulsa "No acepto" | No pide datos; ofrece canales directos |
| 6 | Pulsa "Acepto" + da nombre/modalidad/horario | Cita aparece en el panel como **solicitada**; ningún tuteo |
| 7 | "Quiero hablar con una persona" | `human_requested=true` visible en el panel |
| 8 | Lote en Finca Alborada | Lead `Inmobiliario` con proyecto; no confirma disponibilidad |

**Definición de terminado por fase:** tests en verde, batería 1–8 superada, README actualizado, sin secretos en el diff, `whatsapp-bot/CLAUDE.md` actualizado (sección Estado y pendientes).

---

## 11. Repositorio y despliegue

- El repo `Pantoja18/jys-grupo-web` es **público**. El código del CRM puede ir aquí (no contiene secretos), pero **se recomienda moverlo a un repositorio privado** (`jys-gestion`) antes de producción. Si se mantiene aquí:
  - Actualizar `build.sh` para excluir `crm/` además de `whatsapp-bot/` (si no, Cloudflare Pages publicaría el código).
- Trabajar en la rama `whatsapp-bot` (o una rama `crm` desde ella). **No unir a `main`** sin configurar Pages (`bash build.sh`, salida `dist`).
- El usuario trabaja en **Windows + PowerShell**: comandos compatibles (`curl.exe`, sin `grep`).
- Producción futura (fuera de alcance de este documento): servidor 24/7 (VPS) en lugar de la PC, dominio propio para el webhook, token permanente de Meta (usuario del sistema), validación de firma `X-Hub-Signature-256` con `META_APP_SECRET`.

---

## 12. Plan por fases (orden de implementación)

| Fase | Entregable | Depende de |
|---|---|---|
| **0 – Valeria confiable** | System prompt reforzado (Usted, `<cita>`), modelo comparado (`qwen2.5:7b` / `llama3.1:8b`), rama Claude API opcional, desactivar guardado de ejecuciones | — |
| **1 – Infraestructura y BD** | `postgres` + `crm-api` en Docker, modelos, migraciones, cifrado, `create_admin.py`, `/health` | — |
| **2 – Panel CRM** | Login, roles, Inicio, Citas, Consultas, Contactos, Inmobiliario, Auditoría, Usuarios | 1 |
| **3 – Integración Valeria ↔ CRM** | API `/api/bot/*`, puerta de consentimiento con botones, extracción robusta de citas, batería 1–8 | 0, 1 |
| **4 – Módulos ERP** | Casos, honorarios/pagos, lotes/ventas, reportes | 2 |
| **5 – Endurecimiento** | Retención automática, ARCO completo, respaldos cifrados, TOTP, revisión de seguridad | 2, 3 |

Cada fase: commits pequeños en español, pedir confirmación antes de `git push`, recordar al usuario reimportar/publicar el flujo en n8n cuando cambie `n8n_workflow_template.json`.
