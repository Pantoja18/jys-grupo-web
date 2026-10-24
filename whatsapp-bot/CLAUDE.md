# Contexto del proyecto: Valeria – asistente de WhatsApp de JS CONSULTORES Y ABOGADOS

Lee este archivo completo antes de cualquier cambio. Responde y documenta siempre en español.

## 1. Qué es
- Asistente virtual de WhatsApp llamado **Valeria** para **JS CONSULTORES Y ABOGADOS** (J&S – Estudio Jurídico y Grupo Inmobiliario), de la Dra. Yudith Sandoval, en Supe/Barranca, Perú.
- Funciones: atención inicial, filtrado de casos, recolección de datos con consentimiento y **solicitudes** de cita (el equipo confirma; el bot no ve la agenda real).
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
- Los modelos locales pequeños cumplen las reglas peor que Claude. El modelo local se elige con la batería `pruebas/evaluar_modelos.py` (Fase 0); la garantía del consentimiento y del registro de citas llega en la Fase 3 con código determinista (ver `../crm/ARQUITECTURA.md`).

## 3. Repositorio
- GitHub **público** `Pantoja18/jys-grupo-web`, rama de trabajo `whatsapp-bot`. **No unir a `main`** hasta configurar Cloudflare Pages (ver §9).
- La raíz es la web estática (`index.html`, `terminos.html`, imágenes) desplegada en Cloudflare Pages. La web es la **fuente de verdad** de la base de conocimiento del bot.
- Todo el bot vive en `whatsapp-bot/`:
  - `system_prompt.md`: reglas de Valeria + base de conocimiento. Montado en el contenedor como `/data/system_prompt.md` (solo lectura); los cambios aplican en el siguiente mensaje sin reiniciar.
  - `n8n_workflow_template.json`: flujo importable en n8n.
  - `docker-compose.yml`: n8n local (volumen `n8n_data`, `extra_hosts host.docker.internal:host-gateway`).
  - `.env.example`, `README.md`, `INSTRUCCIONES_PRUEBAS.md`, `.gitignore`.
  - `pruebas/`: `evaluar_modelos.py` (batería conversacional contra Ollama), `test_evaluar_modelos.py` (unittest) y `test_flujo.js` (ejecuta los nodos Code del flujo sin n8n). Los informes van a `pruebas/resultados/` (excluido de git).
- `../crm/ARQUITECTURA.md`: diseño del CRM/ERP y plan por fases (0 a 5).
- `build.sh` (raíz): build de Cloudflare Pages que copia solo el sitio a `dist/` y excluye `whatsapp-bot/` y `crm/`.

## 4. Flujo n8n (`n8n_workflow_template.json`)
- **GET `/webhook/whatsapp`** → IF `hub.verify_token == $env.WEBHOOK_VERIFY_TOKEN` → responde `hub.challenge` (200) o 403.
- **POST `/webhook/whatsapp`** (responde 200 al recibir) →
  1. **Extraer mensaje** (Code): ignora estados, deduplica por `message.id` en static data, normaliza text/interactive/button; otros tipos se pasan como aviso.
  2. **¿Requiere IA?** (IF) → si no hay texto, **Respuesta directa**.
  3. **Preparar solicitud LLM** (Code): valida `LLM_PROVIDER`, lee el system prompt con `fs` (el "Recordatorio final" está dentro de `system_prompt.md`, no en el código), arma `llmBody` para el proveedor elegido (historial + turno actual con fecha/hora de Lima) y devuelve `provider`. Historial por número en `$getWorkflowStaticData('global')`, con `CONVERSATION_MAX_MESSAGES` y `CONVERSATION_TTL_HOURS`.
  4. **¿Usar Claude?** (IF sobre `$json.provider`) → **Claude (Messages API)** (`x-api-key`, `anthropic-version: 2023-06-01`, beta `server-side-fallback-2026-07-01`; timeout 120 s, 3 intentos) u **Ollama (chat local)** (`POST {OLLAMA_URL}/api/chat`, `stream:false`; timeout 300 s, 2 intentos). Ambos con `onError: continueRegularOutput` y cuerpo `$json.llmBody`.
  5. **Procesar respuesta** (Code): según `provider`, lee `message.content` (Ollama) o los bloques `text` de `content` (Claude; `stop_reason: "refusal"` → mensaje de cortesía). Quita `<think>`, extrae todos los `<cita>{...}</cita>` (usa el último válido, tolera bloques de código), retira también etiquetas sueltas, recorta a 4000 caracteres y guarda el turno. Si hay error, envía un mensaje de cortesía.
  6. **Enviar respuesta WhatsApp** (HTTP) y, si hubo cita, **Notificar al estudio** (POST a `FIRM_NOTIFICATION_WEBHOOK_URL`, reemplazable por Sheets/Gmail/CRM).
- El flujo **no usa credenciales de n8n**: todo sale de variables de entorno vía `$env` (por eso `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` y `NODE_FUNCTION_ALLOW_BUILTIN=fs,crypto`).
- La static data solo persiste en ejecuciones de producción (flujo **activo**). Meta llama a `/webhook/`, no a `/webhook-test/`.
- `saveDataSuccessExecution: none` y `saveManualExecutions: false`: las ejecuciones exitosas y manuales no se guardan (protección de datos). Para depurar, cambiarlo temporalmente en *Workflow settings* y restaurarlo después. Las ejecuciones con error sí se guardan.
- Tras cualquier cambio del flujo: `node pruebas/test_flujo.js` (o con Docker, ver §8).

## 5. Variables de entorno (`whatsapp-bot/.env`, nunca en git)
- Meta: `META_ACCESS_TOKEN`, `PHONE_NUMBER_ID`, `META_API_VERSION` (usar la que muestre el panel de Meta), `META_APP_SECRET` (opcional).
- Webhook: `WEBHOOK_VERIFY_TOKEN`, `WEBHOOK_URL` (URL https de ngrok terminada en `/`).
- n8n: `N8N_ENCRYPTION_KEY` (no cambiarla una vez creadas credenciales).
- LLM: `LLM_PROVIDER` (`ollama` | `anthropic`; por defecto `ollama`).
- Ollama: `OLLAMA_URL=http://host.docker.internal:11434`, `OLLAMA_MODEL=llama3:latest` (candidatos: `qwen2.5:7b`, `llama3.1:8b`), `OLLAMA_NUM_CTX=8192` (no bajarlo: el system prompt ocupa ~4.400 tokens y con menos contexto el modelo pierde las reglas), `OLLAMA_NUM_PREDICT`, `OLLAMA_TEMPERATURE`, `OLLAMA_KEEP_ALIVE`.
- Otras: `CONVERSATION_MAX_MESSAGES`, `CONVERSATION_TTL_HOURS`, `BUSINESS_TIMEZONE=America/Lima`, `FIRM_NOTIFICATION_WEBHOOK_URL`.
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
docker run --rm -v "${PWD}:/w" -w /w node:20-alpine node pruebas/test_flujo.js
```

## 9. Estado y pendientes

Estado (24/10/2026): n8n estable en Docker (`js-consultores-n8n`); `N8N_ENCRYPTION_KEY` del `.env` sincronizada con el volumen `n8n_data` (no cambiarla). ngrok Free con dominio fijo `https://feisty-papaya-feast.ngrok-free.dev` (`ngrok http 5678`). App de Meta publicada, Graph `v25.0`; Valeria funciona de extremo a extremo en pruebas. n8n llega a Ollama (`llama3:latest`). Sin clave de Anthropic en uso (el bot usa Ollama). Plan completo por fases: `../crm/ARQUITECTURA.md`.

**Fase 0 – Valeria confiable (implementada en el repo, falta validarla en la PC):**
- `system_prompt.md`: sección *Trato de "Usted"* con 3 ejemplos ❌/✅; consentimiento con el **mensaje fijo** de `ARQUITECTURA.md` §6 y la indicación `Consentimiento: otorgado|rechazado` que inyectará el sistema en la Fase 3; ejemplo literal del bloque `<cita>` en una línea; *Recordatorio final* (antes estaba en el código de n8n).
- Flujo v2: `LLM_PROVIDER=ollama|anthropic` con dos ramas; extracción de `<cita>` más robusta; `saveManualExecutions: false` además de `saveDataSuccessExecution: none`.
- `pruebas/`: batería 1–8 adaptada a la Fase 0 (`evaluar_modelos.py`) y pruebas automáticas (`test_evaluar_modelos.py`, `test_flujo.js`).

Pendientes (en este orden):
1. **Validar la Fase 0 en la PC:** reimportar el flujo v2, desactivar el v1 ("v1 - Ollama local") y publicar el v2; correr la comparación de modelos (`llama3:latest`, `qwen2.5:7b`, `llama3.1:8b`), leer el informe y fijar el mejor en `OLLAMA_MODEL`; repetir la batería por WhatsApp.
2. **Fase 1** – Infraestructura y BD del CRM (`postgres` + `crm-api`), según `../crm/ARQUITECTURA.md`.
3. **Fase 2** – Panel CRM. **Fase 3** – Integración Valeria ↔ CRM: puerta de consentimiento con botones, citas guardadas en el CRM (hoy se pierden: `FIRM_NOTIFICATION_WEBHOOK_URL` está vacía), extracción de cita con segunda llamada al LLM. Esto reemplaza la idea anterior de guardar las citas en Google Sheets.
4. Token permanente de Meta (usuario del sistema); hoy es temporal (24 h).
5. Antes de unir a `main`: en Cloudflare Pages, Build command `bash build.sh` y Build output directory `dist`. Si no, se publicarían `/whatsapp-bot/...` y `/crm/...`.
6. Confirmar el número de derivación de la línea inmobiliaria (hoy 956 771 681, que en la web figura como el WhatsApp jurídico).
7. Producción: validar la firma `X-Hub-Signature-256` con `META_APP_SECRET`; mover la memoria a Redis/Postgres; plantillas de Meta fuera de la ventana de 24 h.
8. **Agrupar mensajes seguidos (debounce) → Fase 3 con PostgreSQL.** No se implementó en n8n porque no es confiable: el static data se carga al iniciar cada ejecución y se guarda completo al terminar, sin bloqueo; ejecuciones simultáneas no comparten cambios y la última sobrescribe. Requiere una cola por número en PostgreSQL (bloqueo de fila). El mismo problema puede hacer que una respuesta lenta del LLM sobrescriba un cambio de consentimiento hecho mientras tanto: también se resuelve al mover `consents` a PostgreSQL.
9. Sincronizar la base de conocimiento cuando cambie la web (hoy es manual).
