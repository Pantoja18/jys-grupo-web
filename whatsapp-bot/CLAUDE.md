# Contexto del proyecto: Valeria – asistente de WhatsApp de JS CONSULTORES Y ABOGADOS

Lee este archivo completo antes de cualquier cambio. Responde y documenta siempre en español.

## 1. Qué es
- Asistente virtual de WhatsApp llamado **Valeria** para **JS CONSULTORES Y ABOGADOS** (J&S – Estudio Jurídico y Grupo Inmobiliario), de la Dra. Yudith Sandoval, en Supe/Barranca, Perú.
- Funciones: atención inicial, filtrado de casos, recolección de datos con consentimiento y **solicitudes** de cita (el equipo confirma; el bot no ve la agenda real). Las solicitudes se guardan cifradas en `datos/citas.jsonl.enc`.
- Debe soportar hasta **3 líneas de WhatsApp**: el flujo responde por el `phone_number_id` que llega en cada mensaje (`value.metadata.phone_number_id`); `PHONE_NUMBER_ID` es solo el valor por defecto.

## 2. Arquitectura actual (pruebas locales, Windows + Docker Desktop)
```
WhatsApp Cloud API (Meta)
   │  webhook GET (verificación) / POST (mensajes)
   ▼
ngrok (dominio estático HTTPS) ──► n8n en Docker (localhost:5678, contenedor js-consultores-n8n)
                                      │  POST /api/chat (stream:false)
                                      ▼
                                   Ollama en el host (http://host.docker.internal:11434, modelo llama3:latest)
   ▲
   └── n8n responde con POST graph.facebook.com/{META_API_VERSION}/{phone_number_id}/messages
```
- **LLM intercambiable con `LLM_PROVIDER`:** `ollama` (por defecto; local, sin costo, modelo `OLLAMA_MODEL`) o `anthropic` (Claude por `POST /v1/messages`, `claude-sonnet-5-5`, requiere `ANTHROPIC_API_KEY`). No hace falta revertir commits para cambiar de proveedor.
- Los modelos locales pequeños cumplen las reglas peor que Claude. El modelo local se elige con la batería `pruebas/evaluar_modelos.py` (Fase 0); por eso el consentimiento lo garantiza código determinista en n8n (*Puerta de consentimiento* + filtro de salida, flujo v3) y las citas se guardan cifradas sin depender de una URL externa. En la Fase 3 ambos pasan al CRM (ver `../crm/ARQUITECTURA.md`).

## 3. Repositorio
- GitHub **público** `Pantoja18/jys-grupo-web`, rama de trabajo `whatsapp-bot`. **No unir a `main`** hasta configurar Cloudflare Pages (ver §9).
- La raíz es la web estática (`index.html`, `terminos.html`, imágenes) desplegada en Cloudflare Pages. La web es la **fuente de verdad** de la base de conocimiento del bot.
- Todo el bot vive en `whatsapp-bot/`:
  - `system_prompt.md`: reglas de Valeria + base de conocimiento. Montado en el contenedor como `/data/system_prompt.md` (solo lectura); los cambios aplican en el siguiente mensaje sin reiniciar.
  - `n8n_workflow_template.json`: flujo importable en n8n.
  - `docker-compose.yml`: n8n local (volumen `n8n_data`, `extra_hosts host.docker.internal:host-gateway`) y, desde la Fase 1, el CRM: `postgres` (`jys-postgres`, sin puertos publicados, volumen `jys_pg_data`), `crm-api` (`jys-crm`, solo `127.0.0.1:8000`) y `crm-tools` (perfil `tools`: migraciones, scripts y pruebas). Red interna `jys` (n8n también está en ella para la Fase 3).
  - `.env.example`, `README.md`, `INSTRUCCIONES_PRUEBAS.md`, `.gitignore`.
  - `herramientas/ver_citas.py`: descifra y muestra `datos/citas.jsonl.enc` (requiere `python -m pip install cryptography`).
  - `datos/` (excluida de git, montada en el contenedor como `/data/datos`): registro cifrado de citas que escribe n8n.
  - `pruebas/`: `evaluar_modelos.py` (batería conversacional contra Ollama), `test_evaluar_modelos.py` y `test_flujo_n8n.py` (unittest; el segundo ejecuta los nodos con Node), `nodos_n8n.js` (arnés que ejecuta los nodos Code fuera de n8n) y `test_flujo.js` (pruebas del flujo sin n8n). Los informes van a `pruebas/resultados/` (excluido de git).
- `../crm/ARQUITECTURA.md`: diseño del CRM/ERP y plan por fases (0 a 5). `../crm/README.md`: cómo levantar, migrar, crear el admin, importar citas y probar el CRM (FastAPI + SQLAlchemy 2 + Alembic, Python 3.12).
- `build.sh` (raíz): build de Cloudflare Pages que copia solo el sitio a `dist/` y excluye `whatsapp-bot/` y `crm/`.

## 4. Flujo n8n (`n8n_workflow_template.json`)
- **GET `/webhook/whatsapp`** → IF `hub.verify_token == $env.WEBHOOK_VERIFY_TOKEN` → responde `hub.challenge` (200) o 403.
- **POST `/webhook/whatsapp`** (responde 200 al recibir) →
  1. **Extraer mensaje** (Code): ignora estados, deduplica por `message.id` en static data, normaliza text/interactive/button (incluido `buttonId`); otros tipos se pasan como aviso.
  2. **Puerta de consentimiento** (Code, sin IA): estado por número en `store.consents` (`pendiente|otorgado|rechazado`, `fecha`, `version`=`POLICY_VERSION`, `canal`, `prompted`). Atiende `/reiniciar` (solo `TEST_NUMBERS`), los botones `consent_si`/`consent_no` y las frases "acepto"/"no acepto"; sin consentimiento otorgado, una intención de cita o un mensaje con datos recibe el **mensaje fijo con botones** sin llamar al LLM. Devuelve `action: "llm" | "reply"` y, para `reply`, `waMessage` (texto o interactivo). También expone `consentStatus`, `consentText` y `consentPrompt` a los nodos siguientes.
  3. **Estado de la cita** (Code, sin IA): solicitud por número en `store.citaFlow` (`estado: en_curso|guardando|registrada|fallida`, `asked`, `datos {nombre, tema, modalidad, horario, correo}`, `correoResuelto`, `urgente`, `contactoHumano`; TTL `CONVERSATION_TTL_HOURS`). Empieza con `consentEvent: "otorgado"` de la puerta, con una intención de cita (consentimiento otorgado) o si el último mensaje de la IA pidió nombre/modalidad/horario/correo. Preguntas fijas, un dato por mensaje (botones `cita_presencial`/`cita_virtual` y `cita_sin_correo`); captura de paso correo y modalidad en cualquier mensaje. Con los datos obligatorios arma `citaToSave` (área por palabras clave del tema) y `citaReplyFail`. Una duda en medio → `action: "llm"` con `citaLine` ("Solicitud de cita: en curso…") y `citaPending`. Rechazo/revocación del consentimiento, "cancelar" y `/reiniciar` borran `citaFlow[from]`.
  4. **¿Llamar a la IA?** (IF `action == "llm"`) → si no, directo a **Guardar cita cifrada**.
  5. **Indicador escribiendo** (HTTP, `onError: continueRegularOutput`, sin reintentos): `status: "read"` + `typing_indicator: {type: "text"}` (dura máx. 25 s).
  6. **Preparar solicitud LLM** (Code): lee la entrada con `$('Estado de la cita')` (no `$input`, para que el indicador no altere los datos); valida `LLM_PROVIDER`, lee el system prompt con `fs` (el "Recordatorio final" está dentro de `system_prompt.md`) y le agrega al final `Consentimiento: <estado>` más `citaLine` si existe (en Claude, como segundo bloque `system` sin caché); arma `llmBody` (historial + turno actual con fecha/hora de Lima). Historial por número en `$getWorkflowStaticData('global')`, con `CONVERSATION_MAX_MESSAGES` y `CONVERSATION_TTL_HOURS`.
  7. **¿Usar Claude?** (IF sobre `$json.provider`) → **Claude (Messages API)** (`x-api-key`, `anthropic-version: 2023-06-01`, beta `server-side-fallback-2026-07-01`; timeout 120 s, 3 intentos) u **Ollama (chat local)** (`POST {OLLAMA_URL}/api/chat`, `stream:false`; timeout 300 s, 2 intentos). Ambos con `onError: continueRegularOutput` y cuerpo `$json.llmBody`.
  8. **Procesar respuesta** (Code): según `provider`, lee `message.content` (Ollama) o los bloques `text` de `content` (Claude; `stop_reason: "refusal"` → mensaje de cortesía). Quita `<think>`, extrae todos los `<cita>{...}</cita>` (usa el último válido, tolera bloques de código), retira también etiquetas sueltas, recorta a 4000 caracteres y guarda el turno. **Filtro de salida:** sin consentimiento otorgado, si la respuesta pide nombre/correo/DNI/teléfono/dirección, se reemplaza por `consentPrompt`. `hasAppointment` solo es verdadero con cita válida **y** consentimiento otorgado (el `<cita>` es solo **respaldo**). Pasa `citaPending`. Si hay error, envía un mensaje de cortesía.
  9. **Guardar cita cifrada** (Code; **todas** las respuestas pasan por aquí): guarda `citaToSave` (origen `flujo`) o, si el flujo no registró ya la solicitud, el `<cita>` de la IA (origen `ia`; sus datos completan `citaFlow`). AES-256-GCM con `CITAS_FILE_KEY` → una línea `v1.<base64(iv|ct|tag)>`, AAD `jys-citas-v1`, en `CITAS_FILE_PATH`; no repite contenido idéntico. Ajusta la respuesta: sin guardar → `citaReplyFail`; si la IA dice "registrada"/"agendada" sin nada guardado → "Aún no he registrado su solicitud de cita." + `citaPending`; en medio de una solicitud agrega `citaPending` a la respuesta de la IA. Corrige el último mensaje del historial si cambió.
  10. **Enviar respuesta WhatsApp** (HTTP; cuerpo `{..., to, ...$json.waMessage}`) y, en paralelo, **¿Avisar por WhatsApp?** → **Avisar al estudio (WhatsApp)** (a `FIRM_NOTIFY_WHATSAPP`, sin datos sensibles) y **¿Webhook configurado?** → **Notificar al estudio (cita)** (`FIRM_NOTIFICATION_WEBHOOK_URL`). Se avisa una vez por conversación (solicitud completa, urgente o con contacto humano); ambos HTTP con `onError: continueRegularOutput`.
- Los nodos Code solo pueden usar `fs` y `crypto` (`NODE_FUNCTION_ALLOW_BUILTIN`); no usar `path` ni otros módulos.
- El flujo **no usa credenciales de n8n**: todo sale de variables de entorno vía `$env` (por eso `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` y `NODE_FUNCTION_ALLOW_BUILTIN=fs,crypto`).
- La static data solo persiste en ejecuciones de producción (flujo **activo**). Meta llama a `/webhook/`, no a `/webhook-test/`.
- `saveDataSuccessExecution: none` y `saveManualExecutions: false`: las ejecuciones exitosas y manuales no se guardan (protección de datos). Para depurar, cambiarlo temporalmente en *Workflow settings* y restaurarlo después. Las ejecuciones con error sí se guardan.
- Tras cualquier cambio del flujo: `node pruebas/test_flujo.js` (o con Docker, ver §8).

## 5. Variables de entorno (`whatsapp-bot/.env`, nunca en git)
- Meta: `META_ACCESS_TOKEN`, `PHONE_NUMBER_ID`, `META_API_VERSION` (usar la que muestre el panel de Meta), `META_APP_SECRET` (opcional).
- Webhook: `WEBHOOK_VERIFY_TOKEN`, `WEBHOOK_URL` (URL https de ngrok terminada en `/`).
- n8n: `N8N_ENCRYPTION_KEY` (no cambiarla una vez creadas credenciales).
- LLM: `LLM_PROVIDER` (`ollama` | `anthropic`; por defecto `ollama`).
- Ollama: `OLLAMA_URL=http://host.docker.internal:11434`, `OLLAMA_MODEL=llama3:latest` (candidatos: `qwen2.5:7b`, `llama3.1:8b`), `OLLAMA_NUM_CTX=8192` (no bajarlo: el system prompt ocupa ~4.400 tokens y con menos contexto el modelo pierde las reglas), `OLLAMA_NUM_PREDICT=200` (respuestas de máx. 2 oraciones; el resumen + `<cita>` ocupa ~150 tokens), `OLLAMA_TEMPERATURE`, `OLLAMA_KEEP_ALIVE`.
- Otras: `CONVERSATION_MAX_MESSAGES`, `CONVERSATION_TTL_HOURS`, `BUSINESS_TIMEZONE=America/Lima`, `POLICY_VERSION` (versión de la política; si cambia, se vuelve a pedir el consentimiento), `TEST_NUMBERS` (números que pueden usar `/reiniciar`).
- Citas: `CITAS_FILE_KEY` (64 hex = 32 bytes; sin ella no se guardan citas y, si se pierde, las guardadas son irrecuperables), `CITAS_FILE_PATH` (lo fija `docker-compose.yml`: `/data/datos/citas.jsonl.enc`), `FIRM_NOTIFY_WHATSAPP` (aviso sin datos sensibles; ventana de 24 h y destinatarios verificados del número de prueba), `FIRM_NOTIFICATION_WEBHOOK_URL` (opcional, envía la solicitud completa).
- Anthropic (solo con `LLM_PROVIDER=anthropic`): `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL=claude-sonnet-5-5`, `ANTHROPIC_MAX_TOKENS=8000`, `ANTHROPIC_EFFORT=low`.

## 6. Reglas de negocio que ningún cambio puede romper
- Valeria trata siempre de **"Usted"**, con tono sobrio, profesional y empático; mensajes cortos para WhatsApp.
- **Prohibición absoluta de asesoría legal**: no analizar casos, no predecir resultados, no interpretar leyes ni documentos, no calcular montos ni plazos, no redactar escritos, no recomendar acciones; tampoco en casos hipotéticos o ante insistencia.
- **Ley N.° 29733**: pedir consentimiento antes de solicitar datos personales; minimizar lo que se guarda y lo que se registra en logs.
- Bloque `<cita>` (lo extrae n8n; el usuario no lo ve): `{"nombre","area","resumen","tipo_cliente","modalidad","preferencia_horaria","correo","urgente","contacto_humano","estado"}`. Si se cambia su formato, actualizar a la vez `system_prompt.md` y el nodo **Procesar respuesta**.
- No inventar datos del estudio: la base de conocimiento sale de la web.

## 7. Reglas de trabajo para Claude Code
- **Nunca** leer en voz alta, imprimir, copiar ni hacer commit de `whatsapp-bot/.env` ni de tokens. Antes de cada commit, verificar con `git status` que `.env` no está incluido.
- El repo es público: no poner secretos, números internos ni datos de clientes en ningún archivo versionado.
- Tras editar `n8n_workflow_template.json`: validar que el JSON sea válido, mantener coherentes los nombres de nodos en `connections` y en las referencias `$('Nombre del nodo')`, y recordar al usuario que debe reimportar el flujo en n8n.
- Tras cambiar `.env` o `docker-compose.yml`: `docker compose up -d --force-recreate` desde `whatsapp-bot/`.
- El usuario trabaja en **Windows / PowerShell**: dar comandos compatibles (`curl.exe`, `copy`, sin `grep`).
- Commits en español, pequeños y descriptivos, en la rama `whatsapp-bot`. Pedir confirmación antes de `git push`.

## 8. Comandos útiles (desde `whatsapp-bot/`)
```powershell
docker compose up -d --force-recreate
docker compose ps
docker compose logs -f n8n
curl.exe -s http://localhost:5678/healthz
docker exec js-consultores-n8n wget -qO- http://host.docker.internal:11434/api/tags
ngrok http 5678
python pruebas/evaluar_modelos.py --modelos llama3:latest qwen2.5:7b llama3.1:8b
python -m unittest discover -s pruebas -v
node pruebas/test_flujo.js
python herramientas/ver_citas.py --env-file .env
docker run --rm -v "${PWD}:/w" -w /w node:20-alpine node pruebas/test_flujo.js
```

## 9. Estado y pendientes

Estado (24/10/2026): n8n estable en Docker (`js-consultores-n8n`); `N8N_ENCRYPTION_KEY` del `.env` sincronizada con el volumen `n8n_data` (no cambiarla). ngrok Free con dominio fijo `https://feisty-papaya-feast.ngrok-free.dev` (`ngrok http 5678`). App de Meta publicada, Graph `v25.0`; Valeria funciona de extremo a extremo en pruebas con el flujo v2 y `llama3:latest` (el único modelo instalado en Ollama). Sin clave de Anthropic en uso. Plan completo por fases: `../crm/ARQUITECTURA.md`.

**Fase 0 – Valeria confiable:** system prompt reforzado (Usted, máx. 2 oraciones, saludo por hora, cita paso a paso como *solicitud*), `LLM_PROVIDER=ollama|anthropic`, batería `evaluar_modelos.py`. Probado por WhatsApp el 24/10/2026: trato de Usted y no asesoría, bien; fallaban el consentimiento (pedía datos antes) y el registro de citas (se perdían), y respondía en 20–40 s.

**Flujo v3 (24/10/2026, importado y probado por WhatsApp):**
- Mejora 1 – *Puerta de consentimiento* determinista con botones Acepto/No acepto, estado por número con fecha y `POLICY_VERSION`, línea `Consentimiento: ...` para el LLM y filtro de salida. Adelanta a n8n la parte de la Fase 3 que no necesita el CRM.
- Mejora 2 – Citas cifradas (AES-256-GCM) en `datos/citas.jsonl.enc` + `herramientas/ver_citas.py`; aviso opcional por WhatsApp (`FIRM_NOTIFY_WHATSAPP`) sin datos sensibles; el webhook queda como opción.
- Mejora 3 – Indicador *escribiendo…* (`status: read` + `typing_indicator`), que no bloquea si falla.
- Mejora 4 – Agrupar mensajes seguidos: **no implementado** (ver pendiente 6).
- Mejora 5 – `/reiniciar` para `TEST_NUMBERS`.
- Mejora 6 – `OLLAMA_NUM_PREDICT=200` (antes 512).
- Mejora 7 – Comparación de modelos **no ejecutada**: `qwen2.5:7b` no está instalado (`ollama pull qwen2.5:7b`). La batería prueba el LLM directamente, sin la puerta de n8n: los casos de consentimiento miden al modelo, no la garantía (que ahora da la puerta).
- Prueba por WhatsApp del 24/10/2026: consentimiento con botones bien, pero la cita **no se guardó**: llama3 dijo "Su solicitud de cita quedó registrada" sin emitir un `<cita>` válido, así que *Guardar cita cifrada* nunca se ejecutó (montaje `./datos`, ruta, clave y permisos estaban bien; `citas` vacío en el static data).
- **v3.1 (corrección):** nodo *Estado de la cita*: n8n lleva la solicitud (paso y datos por número) y la guarda sin depender de `<cita>`, que queda como respaldo; *Guardar cita cifrada* corre en todas las respuestas y nunca se dice "registrada" si no se guardó.
- Pruebas: `node pruebas/test_flujo.js` (58) y `python -m unittest discover -s pruebas` (35), en verde.

**Fase 1 – Infraestructura y BD del CRM (24/10/2026, en el repo; falta levantarla en la PC):**
- `crm/` con FastAPI + SQLAlchemy 2 + Alembic; migración `0001` con `roles`, `users`, `contacts`, `consents`, `leads`, `appointments`, `audit_log` e `imported_citas`. `GET /health` comprueba la BD.
- Dos roles de BD: `POSTGRES_USER` (`jys_owner`, dueño; solo `crm-tools`) y `jys_app` (la aplicación; lo crea `crm/docker/postgres-init/01-app-role.sh`). **Cambio respecto de ARQUITECTURA.md §8:** allí `POSTGRES_USER=jys_app` era también el usuario de la app, pero en la imagen oficial ese usuario es superusuario y podría saltarse los permisos de `audit_log`. `DATABASE_URL` no hace falta en Docker (compose arma la conexión con `POSTGRES_APP_PASSWORD`).
- `audit_log` solo inserción: trigger que rechaza UPDATE/DELETE/TRUNCATE (también al dueño) + `jys_app` solo con SELECT/INSERT. `consents`: la app solo inserta; `ON DELETE RESTRICT`.
- Cifrado AES-256-GCM de nombre, teléfono, correo, perfil de WhatsApp, resumen, notas y TOTP, con versión de clave en cada valor y `key_version` por fila (`CRM_DATA_KEY_V<n>`, `CRM_ACTIVE_KEY_VERSION`); `phone_hash` HMAC-SHA256 (`CRM_HMAC_KEY`). argon2id para contraseñas.
- `scripts/create_admin.py` (getpass) y `scripts/importar_citas.py` (lee `datos/citas.jsonl.enc` con `CITAS_FILE_KEY`; idempotente con `imported_citas`; une versiones de la misma solicitud en 24 h; solo con consentimiento otorgado).
- `crm-api` recibe solo las variables que necesita (no el `.env` completo); el flujo de n8n **no cambió**.
- Pruebas: `docker compose run --rm crm-tools pytest` (29, en verde contra PostgreSQL 16.14; usan la BD `jys_test`).
- Quedó fuera de la Fase 1 (es de fases siguientes): panel y sesiones (`CRM_SECRET_KEY`), `/api/bot/*` (`CRM_BOT_API_KEY`), `interactions`, `re_projects`, `settings`, `backup.ps1`, `purge_expired.py`, re-cifrado masivo.

Pendientes (en este orden):
1. **Importar el v3.1 y probarlo por WhatsApp:** importar `n8n_workflow_template.json`, desactivar el v3 y publicar el v3.1; `/reiniciar` y una cita completa → `ver_citas.py` debe mostrarla (columna/origen `flujo`); probar también una pregunta en medio de la solicitud, "Sin correo" y "cancelar".
2. **Comparar modelos:** `ollama pull qwen2.5:7b` (y opcionalmente `llama3.1:8b`), `python pruebas/evaluar_modelos.py --modelos llama3:latest qwen2.5:7b --repeticiones 1`, fijar el mejor en `OLLAMA_MODEL`.
3. **Levantar la Fase 1 en la PC** (ver `../crm/README.md`): agregar las variables del CRM al `.env` con el bloque de PowerShell del README, `docker compose up -d --build`, `docker compose run --rm crm-tools alembic upgrade head`, `... pytest`, `... python -m scripts.create_admin`, `... python -m scripts.importar_citas`, `curl.exe -s http://127.0.0.1:8000/health`. Comprobar que n8n sigue respondiendo y guardar una copia del `.env` en un gestor de contraseñas.
4. **Fase 2** – Panel CRM. **Fase 3** – Integración Valeria ↔ CRM: pasar el consentimiento (`consents`) y las citas del static data / `datos/citas.jsonl.enc` a la API del CRM (las ya guardadas se importan con `importar_citas.py`), extracción de cita con segunda llamada al LLM. Esto reemplaza la idea anterior de guardar las citas en Google Sheets.
5. Token permanente de Meta (usuario del sistema); hoy es temporal (24 h). La documentación de Meta indica que el indicador de escritura requiere token de sistema o de empresa.
6. **Agrupar mensajes seguidos (debounce) → Fase 3 con PostgreSQL.** No se implementó en n8n porque no es confiable: el static data se carga al iniciar cada ejecución y se guarda completo al terminar, sin bloqueo; ejecuciones simultáneas no comparten cambios y la última sobrescribe. Requiere una cola por número en PostgreSQL (bloqueo de fila). El mismo problema puede hacer que una respuesta lenta del LLM sobrescriba un cambio de consentimiento o del avance de la cita (`citaFlow`) hecho mientras tanto: también se resuelve al mover `consents` y `citaFlow` a PostgreSQL.
7. Aviso al estudio fuera de la ventana de 24 h: plantilla aprobada por Meta para `FIRM_NOTIFY_WHATSAPP`.
8. Antes de unir a `main`: en Cloudflare Pages, Build command `bash build.sh` y Build output directory `dist`. Si no, se publicarían `/whatsapp-bot/...` y `/crm/...`.
9. Confirmar el número de derivación de la línea inmobiliaria (hoy 956 771 681, que en la web figura como el WhatsApp jurídico).
10. Producción: validar la firma `X-Hub-Signature-256` con `META_APP_SECRET`; mover la memoria a Redis/Postgres; respaldo de `datos/` y de `CITAS_FILE_KEY`.
11. Sincronizar la base de conocimiento cuando cambie la web (hoy es manual).
