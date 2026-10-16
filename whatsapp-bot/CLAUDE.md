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
- **LLM actual:** Ollama local con Llama 3, para pruebas sin costo. Llama 3 8B cumple las reglas peor que Claude: sirve para probar la cadena técnica, no para validar el comportamiento final.
- **LLM previsto en producción:** Anthropic Messages API (`claude-sonnet-5-5`). La versión con Claude está en el commit anterior al de Ollama ("Agregar asistente de WhatsApp 'Valeria'..."); para volver, revertir el commit "Cambiar el LLM del flujo a Ollama local".

## 3. Repositorio
- GitHub **público** `Pantoja18/jys-grupo-web`, rama de trabajo `whatsapp-bot`. **No unir a `main`** hasta configurar Cloudflare Pages (ver §9).
- La raíz es la web estática (`index.html`, `terminos.html`, imágenes) desplegada en Cloudflare Pages. La web es la **fuente de verdad** de la base de conocimiento del bot.
- Todo el bot vive en `whatsapp-bot/`:
  - `system_prompt.md`: reglas de Valeria + base de conocimiento. Montado en el contenedor como `/data/system_prompt.md` (solo lectura); los cambios aplican en el siguiente mensaje sin reiniciar.
  - `n8n_workflow_template.json`: flujo importable en n8n.
  - `docker-compose.yml`: n8n local (volumen `n8n_data`, `extra_hosts host.docker.internal:host-gateway`).
  - `.env.example`, `README.md`, `INSTRUCCIONES_PRUEBAS.md`, `.gitignore`.
- `build.sh` (raíz): build de Cloudflare Pages que copia solo el sitio a `dist/` y excluye `whatsapp-bot/`.

## 4. Flujo n8n (`n8n_workflow_template.json`)
- **GET `/webhook/whatsapp`** → IF `hub.verify_token == $env.WEBHOOK_VERIFY_TOKEN` → responde `hub.challenge` (200) o 403.
- **POST `/webhook/whatsapp`** (responde 200 al recibir) →
  1. **Extraer mensaje** (Code): ignora estados, deduplica por `message.id` en static data, normaliza text/interactive/button; otros tipos se pasan como aviso.
  2. **¿Requiere IA?** (IF) → si no hay texto, **Respuesta directa**.
  3. **Preparar solicitud LLM** (Code): lee el system prompt con `fs`, agrega un recordatorio final de reglas, arma `messages` (system + historial + turno actual con fecha/hora de Lima). Historial por número en `$getWorkflowStaticData('global')`, con `CONVERSATION_MAX_MESSAGES` y `CONVERSATION_TTL_HOURS`.
  4. **Ollama (chat local)** (HTTP Request): `POST {OLLAMA_URL}/api/chat`, `stream:false`, `options: {temperature, num_ctx, num_predict}`, timeout 300 s, `onError: continueRegularOutput`.
  5. **Procesar respuesta** (Code): lee `message.content`, quita `<think>`, extrae y retira `<cita>{...}</cita>` (tolera bloques de código), recorta a 4000 caracteres, guarda el turno. Si hay error, envía un mensaje de cortesía.
  6. **Enviar respuesta WhatsApp** (HTTP) y, si hubo cita, **Notificar al estudio** (POST a `FIRM_NOTIFICATION_WEBHOOK_URL`, reemplazable por Sheets/Gmail/CRM).
- El flujo **no usa credenciales de n8n**: todo sale de variables de entorno vía `$env` (por eso `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` y `NODE_FUNCTION_ALLOW_BUILTIN=fs,crypto`).
- La static data solo persiste en ejecuciones de producción (flujo **activo**). Meta llama a `/webhook/`, no a `/webhook-test/`.
- `saveDataSuccessExecution: none`: las ejecuciones exitosas no se guardan (protección de datos). Para depurar, cambiarlo temporalmente en *Workflow settings* y restaurarlo después.

## 5. Variables de entorno (`whatsapp-bot/.env`, nunca en git)
- Meta: `META_ACCESS_TOKEN`, `PHONE_NUMBER_ID`, `META_API_VERSION` (usar la que muestre el panel de Meta), `META_APP_SECRET` (opcional).
- Webhook: `WEBHOOK_VERIFY_TOKEN`, `WEBHOOK_URL` (URL https de ngrok terminada en `/`).
- n8n: `N8N_ENCRYPTION_KEY` (no cambiarla una vez creadas credenciales).
- Ollama: `OLLAMA_URL=http://host.docker.internal:11434`, `OLLAMA_MODEL=llama3:latest`, `OLLAMA_NUM_CTX=8192` (no bajarlo: con menos contexto el modelo pierde las reglas), `OLLAMA_NUM_PREDICT`, `OLLAMA_TEMPERATURE`, `OLLAMA_KEEP_ALIVE`.
- Otras: `CONVERSATION_MAX_MESSAGES`, `CONVERSATION_TTL_HOURS`, `BUSINESS_TIMEZONE=America/Lima`, `FIRM_NOTIFICATION_WEBHOOK_URL`, `ANTHROPIC_API_KEY`/`ANTHROPIC_MODEL` (sin uso con Ollama).

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
ngrok http --url=SU-DOMINIO.ngrok-free.app 5678
```

## 9. Estado y pendientes

Estado (16/10/2026): n8n estable en Docker (`js-consultores-n8n`); `N8N_ENCRYPTION_KEY` del `.env` sincronizada con el volumen `n8n_data` (no cambiarla). Flujo importado y publicado como "v1 - Ollama local". ngrok Free con dominio fijo `https://feisty-papaya-feast.ngrok-free.dev` (se inicia con `ngrok http 5678`; el plan Free no permite dominios personalizados). `WEBHOOK_URL` apunta a ese dominio; la verificación GET por ngrok responde `12345`. n8n llega a Ollama (`llama3:latest`). Asistente de n8n desactivado. Sin clave de Anthropic (el bot usa solo Ollama). Guías detalladas: `ESTADO_PROYECTO.md` y `GUIA_META_NGROK.md`.

1. Crear la app en Meta Developers y completar `META_ACCESS_TOKEN`, `PHONE_NUMBER_ID` y `META_API_VERSION` en el `.env`; luego `docker compose up -d --force-recreate`.
2. Configurar el webhook en Meta con `https://feisty-papaya-feast.ngrok-free.dev/webhook/whatsapp` (token = `WEBHOOK_VERIFY_TOKEN`) y suscribir el campo `messages`.
3. Prueba de extremo a extremo por WhatsApp.
4. Reemplazar el nodo "Notificar al estudio (cita)" por Google Sheets para guardar las citas (hoy se pierden: `FIRM_NOTIFICATION_WEBHOOK_URL` está vacía).
5. Antes de unir a `main`: en Cloudflare Pages, Build command `bash build.sh` y Build output directory `dist`. Si no, se publicaría `/whatsapp-bot/...` (incluido este archivo).
6. Confirmar el número de derivación de la línea inmobiliaria (hoy 956 771 681, que en la web figura como el WhatsApp jurídico).
7. Producción: validar la firma `X-Hub-Signature-256` con `META_APP_SECRET`; mover la memoria a Redis/Postgres; debounce de mensajes seguidos; plantillas de Meta fuera de la ventana de 24 h; token permanente de Meta (usuario del sistema); volver a Claude o validar a fondo el modelo elegido.
8. Sincronizar la base de conocimiento cuando cambie la web (hoy es manual).
