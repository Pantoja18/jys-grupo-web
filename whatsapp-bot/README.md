# JS CONSULTORES Y ABOGADOS – Asistente virtual de WhatsApp

Asistente conversacional para la atención inicial de **JS CONSULTORES Y ABOGADOS** por WhatsApp. Recibe las consultas, identifica el área legal, registra solicitudes de cita y deriva al equipo, **sin brindar asesoría legal**.

## Arquitectura

```
┌──────────────┐   webhook (POST)   ┌──────────────────────────────┐   HTTP     ┌───────────────────┐
│   Usuario    │ ─────────────────▶ │             n8n              │ ─────────▶ │  Ollama (local)   │
│  (WhatsApp)  │                    │                              │            │  /api/chat        │
│              │ ◀───────────────── │  extraer → historial →       │ ◀───────── │  o Claude (API)   │
└──────────────┘  Graph API /messages│  llamar al LLM → responder   │            └───────────────────┘
       ▲                            └──────────────┬───────────────┘
       │                                           │ solicitud de cita (<cita> JSON)
 WhatsApp Cloud API (Meta)                         ▼
                                       Webhook del estudio (Sheets / CRM / correo)
```

| Componente | Rol |
|---|---|
| **WhatsApp Cloud API** (Meta) | Entrega cada mensaje entrante a n8n por webhook y envía las respuestas mediante `POST /{phone-number-id}/messages`. |
| **n8n** | Orquestador: valida el webhook, extrae el mensaje, mantiene el historial por número, llama al LLM (Ollama o Claude, según `LLM_PROVIDER`), envía la respuesta y notifica las solicitudes de cita. |
| **Ollama** (local, sin API key) o **Claude** (`LLM_PROVIDER=anthropic`) | Genera la respuesta siguiendo `system_prompt.md`: tono formal, filtrado del caso, agendamiento y el límite de no dar asesoría legal. |

### Sobre el modelo

El proveedor se elige con **`LLM_PROVIDER`** en el `.env`; el nodo **¿Usar Claude?** envía la petición a la rama correspondiente y **Procesar respuesta** entiende ambos formatos.

- **`ollama`** (por defecto): modelo local (`OLLAMA_MODEL`), sin enviar conversaciones a servicios externos. n8n llama a `POST {OLLAMA_URL}/api/chat` con `stream: false`, el system prompt como primer mensaje `system`, `num_ctx: 8192` (el system prompt ocupa ~4.400 tokens; no lo baje), `temperature: 0.3` y `keep_alive: 30m`.
- **`anthropic`**: Claude por `POST https://api.anthropic.com/v1/messages` con `ANTHROPIC_API_KEY`, modelo `claude-sonnet-5-5`, `thinking: {type: "adaptive"}`, `output_config.effort` (`ANTHROPIC_EFFORT`, por defecto `low`), `fallbacks: "default"` (beta `server-side-fallback-2026-07-01`) y el system prompt con caché. Envía a WhatsApp solo los bloques de texto y responde con cortesía si la API rechaza la solicitud (`stop_reason: "refusal"`).

**Consentimiento:** ya no depende del modelo. La *Puerta de consentimiento* de n8n pide la autorización con botones y la registra sin pasar por la IA, y un filtro de salida bloquea cualquier pedido de datos sin consentimiento (ver *Flujo del workflow*).

**Elegir el modelo local:** los modelos pequeños cumplen las reglas largas con menos fiabilidad (en las pruebas del 17/10/2026, `llama3` tuteó, pidió datos sin consentimiento y no emitió `<cita>`). Compare candidatos con la batería automática antes de cambiar `OLLAMA_MODEL` (ver `INSTRUCCIONES_PRUEBAS.md` → *Comparar modelos*). Las reglas críticas se refuerzan en `system_prompt.md` (sección *Trato de "Usted"* con ejemplos, mensaje fijo de consentimiento, ejemplo literal del bloque `<cita>` y *Recordatorio final*), pero la garantía real del consentimiento y del registro de citas llegará en la Fase 3 (`crm/ARQUITECTURA.md`), con código determinista en n8n.

## Flujo del workflow (`n8n_workflow_template.json`)

**Verificación (GET `/webhook/whatsapp`)**
1. Meta envía `hub.verify_token` y `hub.challenge`.
2. Si el token coincide con `WEBHOOK_VERIFY_TOKEN`, se devuelve el `challenge`; si no, `403`.

**Mensajes (POST `/webhook/whatsapp`)**
1. **Webhook**: responde `200` de inmediato (Meta reintenta si tarda).
2. **Extraer mensaje**: ignora eventos de estado (`sent`, `delivered`, `read`), descarta duplicados por `message.id` y normaliza texto, botones (incluido su `id`) y listas. Audios, imágenes y documentos se pasan como aviso para que el asistente pida la consulta por texto.
3. **Puerta de consentimiento** (código determinista, sin IA; Ley N.° 29733): guarda por número el estado `pendiente` / `otorgado` / `rechazado`, con fecha, canal y `POLICY_VERSION`.
   - Si no hay consentimiento otorgado y el mensaje muestra intención de cita o trae datos (cita, agendar, consulta, reunión, abogado, hablar con una persona, "me llamo…", un correo, un DNI o teléfono), **no llama al LLM**: envía el mensaje fijo de consentimiento como mensaje interactivo con los botones **Acepto** (`consent_si`) y **No acepto** (`consent_no`). Los datos que la persona haya escrito en ese mensaje no se guardan en el historial.
   - Procesa los botones y las frases "acepto" / "no acepto" (y "sí" / "no" justo después de la pregunta) sin la IA y responde con un mensaje fijo. Si rechaza, no se piden datos y se dan los canales directos del estudio.
   - Un consentimiento otorgado con otra `POLICY_VERSION` se vuelve a pedir.
   - **¿Llamar a la IA?**: los mensajes fijos van directo a **Enviar respuesta WhatsApp**.
4. **Preparar solicitud LLM**: lee `system_prompt.md`, le agrega al final la línea `Consentimiento: otorgado|pendiente|rechazado`, recupera el historial del número (máx. `CONVERSATION_MAX_MESSAGES`, se reinicia tras `CONVERSATION_TTL_HOURS`) y arma el cuerpo de la petición.
5. **¿Usar Claude?** → **Claude (Messages API)** si `LLM_PROVIDER=anthropic` (tiempo de espera 2 min, 3 intentos), o **Ollama (chat local)** en otro caso (`POST {OLLAMA_URL}/api/chat`, 5 min, 2 intentos).
6. **Procesar respuesta**: si el LLM falla o rechaza la solicitud responde con un mensaje de cortesía, retira todos los bloques `<cita>{…}</cita>` (usa el último válido) y las etiquetas sueltas, recorta a los límites de WhatsApp y guarda el turno en memoria. **Filtro de salida:** si no hay consentimiento otorgado y la respuesta pide nombre, correo, DNI, teléfono o dirección, se reemplaza por el mensaje de consentimiento con botones. Sin consentimiento, ningún bloque `<cita>` se registra.
7. **Enviar respuesta WhatsApp** (texto o mensaje interactivo, según `waMessage`).
8. Si hubo bloque `<cita>` válido **con consentimiento otorgado**: **Guardar cita cifrada** en `datos/citas.jsonl.enc` (ver *Citas*) y, una sola vez por conversación (cuando la solicitud está completa, es urgente o pide atención humana), **Avisar al estudio (WhatsApp)** si `FIRM_NOTIFY_WHATSAPP` tiene un número y **Notificar al estudio (cita)** si `FIRM_NOTIFICATION_WEBHOOK_URL` tiene una URL.

### Flujo conversacional (definido en `system_prompt.md`)

Saludo → filtrado del caso (área, resumen, urgencia, persona natural/empresa) → consentimiento para datos personales (Ley N.° 29733) → recolección de datos de la cita → cierre con resumen. El asistente registra **solicitudes** de cita; un miembro del equipo confirma la fecha y hora reales. Incluye reglas para urgencias, plazos inminentes, pedidos de atención humana y clientes actuales.

## Estructura

```
.
├── README.md                    # Este documento
├── system_prompt.md             # Instrucciones y base de conocimiento del asistente
├── n8n_workflow_template.json   # Flujo importable en n8n
├── docker-compose.yml           # n8n local (puerto 5678, volumen n8n_data)
├── INSTRUCCIONES_PRUEBAS.md     # Docker + Ollama + ngrok + webhook de Meta (resumen)
├── GUIA_META_NGROK.md           # Paso a paso detallado de Meta y ngrok
├── ESTADO_PROYECTO.md           # Estado actual y próximos pasos
├── CLAUDE.md                    # Contexto y reglas para Claude Code
├── herramientas/
│   └── ver_citas.py             # Descifra y muestra datos/citas.jsonl.enc
├── datos/                       # Registro cifrado de citas (lo crea n8n; excluido de git)
├── pruebas/
│   ├── evaluar_modelos.py       # Batería conversacional contra Ollama (comparar modelos)
│   ├── test_evaluar_modelos.py  # Pruebas del evaluador y del system prompt
│   ├── test_flujo_n8n.py        # Pruebas de los nodos del flujo desde unittest (usa Node)
│   ├── nodos_n8n.js             # Ejecuta los nodos Code del flujo fuera de n8n (lo usan las pruebas)
│   └── test_flujo.js            # Pruebas de los nodos Code del flujo (sin n8n)
├── .env.example                 # Variables de entorno
└── .gitignore
```

## Puesta en marcha

1. **Requisitos**: n8n (≥ 1.x, autoalojado y accesible por HTTPS), una app en Meta for Developers con el producto WhatsApp, y [Ollama](https://ollama.com) instalado en el mismo equipo con el modelo descargado (`ollama pull llama3`).
2. **Variables**: `cp .env.example .env` y complete los valores.
3. **Levantar n8n**: para pruebas locales use `docker compose up -d` (ver `INSTRUCCIONES_PRUEBAS.md`), que ya monta `system_prompt.md`. Si usa otro despliegue, monte el archivo en la ruta de `SYSTEM_PROMPT_PATH`. Ejemplo con `docker run`:
   ```bash
   docker run -d --name n8n -p 5678:5678 --env-file .env \
     -v n8n_data:/home/node/.n8n \
     -v "$(pwd)/system_prompt.md:/data/system_prompt.md:ro" \
     docker.n8n.io/n8nio/n8n
   ```
4. **Importar el flujo**: en n8n, *Workflows → Import from File* → `n8n_workflow_template.json`.
5. **Configurar Meta**: en *WhatsApp → Configuración → Webhook*, use como URL de devolución `https://<su-dominio>/webhook/whatsapp`, el mismo `WEBHOOK_VERIFY_TOKEN`, y suscríbase al campo **`messages`**.
6. **Activar** el workflow y enviar un mensaje de prueba al número.

## Personalización

La sección **Base de conocimiento** de `system_prompt.md` se tomó de la web oficial (`../index.html`), que es la fuente de verdad. **Cuando cambie la web, actualice también esa sección** (la sincronización es manual por ahora):

| Dato | Valor actual |
|---|---|
| Oficina | Jr. Bolognesi N.° 533, Supe Pueblo, Barranca (presencial y virtual) |
| Horario | Lunes a Viernes de 9:00 AM a 6:00 PM |
| Servicios jurídicos | Alimentos, tenencia, sucesión intestada, prescripción adquisitiva, saneamiento de predios, rectificación de partida, daños a la propiedad, minutas, contratos, conciliación, cartas notariales, actas de matrimonio |
| Línea inmobiliaria | Finca Alborada y Mirador de Cascajal; derivación a la Dra. Yudith Sandoval (WhatsApp 956 771 681) |
| Consulta inicial | Previa coordinación (no se indican montos) |

Tras editar el archivo, los cambios aplican en el siguiente mensaje (no requiere reimportar el flujo).

Para enviar las citas a otro destino, reemplace el nodo **Notificar al estudio (cita)** por Google Sheets, Gmail, Google Calendar o el CRM del estudio (en la Fase 3 se guardarán en el CRM, ver `../crm/ARQUITECTURA.md`).

## Citas: registro cifrado y aviso al estudio

Cada solicitud de cita (bloque `<cita>` válido, **solo con consentimiento otorgado**) se agrega como una línea cifrada en `whatsapp-bot/datos/citas.jsonl.enc`:

- Cifrado **AES-256-GCM** (módulo `crypto` de n8n) con la clave `CITAS_FILE_KEY` (32 bytes en hex). Cada línea es `v1.` + base64(IV de 12 bytes | texto cifrado | etiqueta de 16 bytes), con AAD `jys-citas-v1`. Incluye fecha, teléfono, línea de WhatsApp, versión y fecha del consentimiento y los campos del bloque `<cita>`.
- La carpeta `datos/` se monta en el contenedor como `/data/datos` y está excluida de git. Haga copias de respaldo de esa carpeta **y** guarde la clave en un gestor de contraseñas: **sin la clave, las citas son irrecuperables**.
- El modelo reenvía el bloque en cada turno: si el contenido no cambió no se repite la línea; si cambió (p. ej. agregó el correo), se agrega una nueva.
- Si `CITAS_FILE_KEY` falta o está mal formada, la cita **no** se guarda; el aviso al estudio lo indica y el log de n8n muestra el motivo (sin datos personales).

Generar la clave en PowerShell (una sola vez; cópiela en `CITAS_FILE_KEY=` del `.env` y en su gestor de contraseñas):
```powershell
$b = New-Object byte[] 32; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b); -join ($b | ForEach-Object { $_.ToString('x2') })
```

Ver las citas (desde `whatsapp-bot/`; requiere `python -m pip install cryptography`):
```powershell
python herramientas/ver_citas.py --env-file .env          # toma solo CITAS_FILE_KEY del .env
python herramientas/ver_citas.py --env-file .env --todas  # todas las versiones de cada solicitud
```
También acepta la clave en `$env:CITAS_FILE_KEY`. Los datos descifrados solo se muestran en pantalla.

**Aviso por WhatsApp (opcional):** si `FIRM_NOTIFY_WHATSAPP` tiene un número (solo dígitos, con código de país), n8n le envía, desde la misma línea que atendió, un aviso **sin datos sensibles**: *"Nueva solicitud de cita – área: X – modalidad: Y. Revise el registro."* Limitaciones:
- **Ventana de 24 h de Meta:** el mensaje libre solo se entrega si ese número escribió a la línea del bot en las últimas 24 h. Para avisos sin esa restricción hace falta una plantilla aprobada por Meta (pendiente).
- **Número de prueba de Meta:** solo envía a destinatarios verificados (*WhatsApp → Configuración de la API → Para*); agregue allí el número del estudio.
- Si el envío falla, el flujo continúa (`onError: continueRegularOutput`): la cita ya quedó guardada.

**Webhook (opcional):** `FIRM_NOTIFICATION_WEBHOOK_URL` sigue disponible y recibe la solicitud completa en JSON (incluye datos personales: úselo solo con un destino seguro).

## Seguridad y producción

- **Firma de Meta**: valide la cabecera `X-Hub-Signature-256` con `META_APP_SECRET` (HMAC-SHA256 del cuerpo crudo; active *Raw Body* en el webhook y compárela en un nodo Code con `crypto`) antes de procesar mensajes.
- **Memoria**: la plantilla guarda el historial en *static data* de n8n, que solo persiste en ejecuciones de producción y no escala a varios workers. Para volumen real, use Redis o Postgres.
- **Mensajes simultáneos**: si un usuario envía varios mensajes seguidos, pueden procesarse en paralelo. Considere un *debounce* (agrupar mensajes durante unos segundos) o una cola por número.
- **Ventana de 24 h**: WhatsApp solo permite mensajes libres dentro de las 24 h posteriores al último mensaje del usuario. Para confirmaciones posteriores, use plantillas aprobadas por Meta.
- **Citas**: el registro `datos/citas.jsonl.enc` está cifrado, pero la clave vive en `.env` del mismo equipo; active el cifrado de disco (BitLocker / Cifrado del dispositivo) y restrinja el acceso al equipo.
- **Datos personales**: las conversaciones contienen datos sensibles. Limite la retención (el flujo ya trae `saveDataSuccessExecution: none` y `saveManualExecutions: false`; si los cambia para depurar, restáurelos; las ejecuciones con error sí se guardan y pueden contener el mensaje), restrinja el acceso a n8n y cumpla la Ley N.° 29733 y su reglamento.
- **Secretos**: mantenga `.env` fuera del repositorio (incluido en `.gitignore`).

## Aviso

El asistente solo brinda información general sobre los servicios del estudio y no reemplaza la evaluación de un abogado. Toda orientación legal la brinda el equipo de JS CONSULTORES Y ABOGADOS en la consulta.

## Ubicación en el repositorio y despliegue de la web

Este bot vive en `whatsapp-bot/` dentro del repositorio de la web (`jys-grupo-web`), que es **público**: todo lo que está aquí, incluido `system_prompt.md`, es visible en GitHub. Las credenciales van solo en `.env`, que está excluido por `.gitignore` (raíz y esta carpeta).

Para que Cloudflare Pages **no publique** esta carpeta en el sitio, el proyecto de Pages debe usar el script `build.sh` de la raíz, que copia solo los archivos del sitio a `dist/`:

| Ajuste (Pages → Settings → Builds & deployments) | Valor |
|---|---|
| Build command | `bash build.sh` |
| Build output directory | `dist` |
| Build watch paths → Exclude (opcional) | `whatsapp-bot/*` (evita redeploys por cambios del bot) |

Sin ese ajuste, Pages publica la raíz del repositorio y `whatsapp-bot/` quedaría accesible en el sitio (por ejemplo, `/whatsapp-bot/system_prompt.md`).
