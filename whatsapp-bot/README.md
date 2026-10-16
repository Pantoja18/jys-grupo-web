# JS CONSULTORES Y ABOGADOS – Asistente virtual de WhatsApp

Asistente conversacional para la atención inicial de **JS CONSULTORES Y ABOGADOS** por WhatsApp. Recibe las consultas, identifica el área legal, registra solicitudes de cita y deriva al equipo, **sin brindar asesoría legal**.

## Arquitectura

```
┌──────────────┐   webhook (POST)   ┌──────────────────────────────┐   HTTP     ┌───────────────────┐
│   Usuario    │ ─────────────────▶ │             n8n              │ ─────────▶ │  Ollama (local)   │
│  (WhatsApp)  │                    │                              │            │  /api/chat        │
│              │ ◀───────────────── │  extraer → historial →       │ ◀───────── │  llama3           │
└──────────────┘  Graph API /messages│  llamar al LLM → responder   │            └───────────────────┘
       ▲                            └──────────────┬───────────────┘
       │                                           │ solicitud de cita (<cita> JSON)
 WhatsApp Cloud API (Meta)                         ▼
                                       Webhook del estudio (Sheets / CRM / correo)
```

| Componente | Rol |
|---|---|
| **WhatsApp Cloud API** (Meta) | Entrega cada mensaje entrante a n8n por webhook y envía las respuestas mediante `POST /{phone-number-id}/messages`. |
| **n8n** | Orquestador: valida el webhook, extrae el mensaje, mantiene el historial por número, llama al modelo local, envía la respuesta y notifica las solicitudes de cita. |
| **Ollama** con **Llama 3** (en el mismo equipo, sin API key) | Genera la respuesta siguiendo `system_prompt.md`: tono formal, filtrado del caso, agendamiento y el límite de no dar asesoría legal. |

### Sobre el modelo

El modelo corre localmente con **Ollama** (`ollama pull llama3`); no se envían conversaciones a servicios externos de IA ni hace falta API key. n8n llama a `POST {OLLAMA_URL}/api/chat` (por defecto `http://host.docker.internal:11434`) con:

- `stream: false` y el system prompt como primer mensaje `system`, seguido del historial.
- `options.num_ctx: 8192`: el system prompt ocupa ~3.000 tokens y el contexto por defecto de Ollama lo truncaría. 8192 es el máximo de Llama 3; con historiales largos, baje `CONVERSATION_MAX_MESSAGES`.
- `temperature: 0.3` para respuestas más consistentes con las reglas, y `keep_alive: 30m` para no recargar el modelo en cada mensaje.
- Un recordatorio breve de las reglas clave (español, "Usted", sin asesoría legal, `<cita>` en JSON de una línea) al final del system prompt, porque los modelos pequeños tienden a olvidarlas en conversaciones largas. El flujo también acepta el bloque `<cita>` envuelto en bloques de código.

**Limitación importante:** Llama 3 (8B) sigue instrucciones largas con menos fiabilidad que un modelo grande. Puede romper la prohibición de asesoría legal, dejar de tratar de "Usted" o emitir el bloque `<cita>` mal formado (el flujo descarta etiquetas incompletas para que el usuario no las vea). Pruebe la conversación a fondo antes de usarlo con clientes reales; si falla, use un modelo mayor en `OLLAMA_MODEL` (por ejemplo `llama3:70b`, si su equipo lo soporta).

## Flujo del workflow (`n8n_workflow_template.json`)

**Verificación (GET `/webhook/whatsapp`)**
1. Meta envía `hub.verify_token` y `hub.challenge`.
2. Si el token coincide con `WEBHOOK_VERIFY_TOKEN`, se devuelve el `challenge`; si no, `403`.

**Mensajes (POST `/webhook/whatsapp`)**
1. **Webhook**: responde `200` de inmediato (Meta reintenta si tarda).
2. **Extraer mensaje**: ignora eventos de estado (`sent`, `delivered`, `read`), descarta duplicados por `message.id` y normaliza texto, botones y listas. Audios, imágenes y documentos se pasan como aviso para que el asistente pida la consulta por texto.
3. **Preparar solicitud LLM**: lee `system_prompt.md`, recupera el historial del número (máx. `CONVERSATION_MAX_MESSAGES`, se reinicia tras `CONVERSATION_TTL_HOURS`) y arma el cuerpo de la petición.
4. **Ollama (chat local)**: `POST {OLLAMA_URL}/api/chat` (por defecto `http://host.docker.internal:11434`), con tiempo de espera de 5 minutos y un reintento.
5. **Procesar respuesta**: si Ollama falla responde con un mensaje de cortesía, retira el bloque `<cita>{…}</cita>`, recorta a los límites de WhatsApp y guarda el turno en memoria.
6. **Enviar respuesta WhatsApp** y, si hubo bloque `<cita>`, **Notificar al estudio** con el JSON de la solicitud.

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

Para enviar las citas a otro destino, reemplace el nodo **Notificar al estudio (cita)** por Google Sheets, Gmail, Google Calendar o el CRM del estudio.

## Seguridad y producción

- **Firma de Meta**: valide la cabecera `X-Hub-Signature-256` con `META_APP_SECRET` (HMAC-SHA256 del cuerpo crudo; active *Raw Body* en el webhook y compárela en un nodo Code con `crypto`) antes de procesar mensajes.
- **Memoria**: la plantilla guarda el historial en *static data* de n8n, que solo persiste en ejecuciones de producción y no escala a varios workers. Para volumen real, use Redis o Postgres.
- **Mensajes simultáneos**: si un usuario envía varios mensajes seguidos, pueden procesarse en paralelo. Considere un *debounce* (agrupar mensajes durante unos segundos) o una cola por número.
- **Ventana de 24 h**: WhatsApp solo permite mensajes libres dentro de las 24 h posteriores al último mensaje del usuario. Para confirmaciones posteriores, use plantillas aprobadas por Meta.
- **Datos personales**: las conversaciones contienen datos sensibles. Limite la retención (`saveDataSuccessExecution: none` ya está configurado), restrinja el acceso a n8n y cumpla la Ley N.° 29733 y su reglamento.
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
