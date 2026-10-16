# Estado del proyecto – Valeria, asistente de WhatsApp de J&S

*Actualizado: 16/10/2026.* Este archivo resume el contexto del proyecto para retomarlo en cualquier sesión.

## Resumen

- **Qué es:** asistente virtual de WhatsApp **"Valeria"** para **JS CONSULTORES Y ABOGADOS** (en la web: *J&S – Estudio Jurídico y Grupo Inmobiliario*, Dra. Yudith Sandoval, Supe/Barranca, Perú).
- **Arquitectura actual (pruebas):** WhatsApp Cloud API (Meta) ⇄ ngrok ⇄ **n8n** (Docker, puerto 5678) ⇄ **Ollama** con **`llama3`** en el mismo equipo.
- **Repositorio:** `Pantoja18/jys-grupo-web` (público), carpeta `whatsapp-bot/`, rama **`whatsapp-bot`**. La web (HTML estático en Cloudflare Pages) está en la raíz y es la fuente de verdad de la base de conocimiento.

## Hecho y verificado

| Elemento | Estado |
|---|---|
| Docker + n8n (`docker compose up -d`, volumen `n8n_data`) | ✅ Funcionando en local, responde HTTP 200 |
| Ollama con `llama3` | ✅ Funcionando en local |
| Flujo n8n (`n8n_workflow_template.json`) | ✅ Llama a `{OLLAMA_URL}/api/chat`; JSON válido |
| `system_prompt.md` (reglas de Valeria) | ✅ Completo |
| Rama `whatsapp-bot` en GitHub | ✅ Subida |
| Conexión con Meta (webhook + número de prueba) | ⏳ Siguiente paso: ver `GUIA_META_NGROK.md` |

## Reglas de Valeria (`system_prompt.md`, no modificar sin revisión del estudio)

- **Prohibición absoluta de asesoría legal:** no analiza casos, no predice resultados, no interpreta leyes, no calcula montos ni plazos, no redacta escritos, ni siquiera ante insistencia o casos "hipotéticos".
- **Tono:** sobrio, profesional, formal y empático; siempre trato de **"Usted"**; mensajes breves para WhatsApp.
- **Ley N.° 29733:** pide consentimiento antes de solicitar datos personales.
- **Citas:** registra *solicitudes* (no ve la agenda real). Al detectar intención de agendar, agrega al final un bloque que n8n extrae y no ve el usuario:
  `<cita>{"nombre":"","area":"<servicio>|Inmobiliario|Otro","resumen":"","tipo_cliente":"persona_natural|empresa","modalidad":"presencial|virtual|por_definir","preferencia_horaria":"","correo":"","urgente":false,"contacto_humano":false,"estado":"incompleta|completa"}</cita>`
- **Base de conocimiento (de la web):** Jr. Bolognesi N.° 533, Supe Pueblo, Barranca; lunes a viernes de 9:00 AM a 6:00 PM; 12 servicios jurídicos (alimentos, tenencia, sucesión intestada, prescripción adquisitiva, saneamiento, etc.); línea inmobiliaria (Finca Alborada, Mirador de Cascajal) con derivación a la **Dra. Yudith Sandoval, WhatsApp 956 771 681**.

## Archivos de `whatsapp-bot/`

| Archivo | Para qué sirve |
|---|---|
| `system_prompt.md` | Reglas y base de conocimiento de Valeria (se lee en cada mensaje; editar no requiere reiniciar) |
| `n8n_workflow_template.json` | Flujo de n8n: verificación GET de Meta, mensajes POST, Ollama, respuesta por WhatsApp, aviso de citas |
| `docker-compose.yml` | n8n local con `host.docker.internal` hacia Ollama |
| `.env.example` | Variables: `OLLAMA_URL`, `OLLAMA_MODEL`, `META_ACCESS_TOKEN`, `PHONE_NUMBER_ID`, `WEBHOOK_VERIFY_TOKEN`, `N8N_ENCRYPTION_KEY`, `WEBHOOK_URL` y opcionales |
| `INSTRUCCIONES_PRUEBAS.md` | Resumen de la prueba local en 4 pasos y comandos de estado |
| `GUIA_META_NGROK.md` | **Siguiente paso:** paso a paso de Meta y ngrok, pruebas y errores |
| `README.md` | Arquitectura, flujo y seguridad |

## Próximos pasos

1. **Conectar Meta y ngrok** siguiendo `GUIA_META_NGROK.md` (credenciales → prueba `hello_world` → ngrok con dominio fijo → webhook → prueba de punta a punta).
2. **Probar los casos críticos** de la tabla E.3 de esa guía. Llama 3 (8B) cumple reglas largas con menos fiabilidad; si rompe la prohibición de asesoría legal, usar un modelo mayor o volver a Claude para producción (las variables `ANTHROPIC_*` se conservan en `.env.example`).
3. **Cloudflare Pages, antes de unir la rama a `main`:** *Build command* `bash build.sh`, *Build output directory* `dist` (opcional: excluir `whatsapp-bot/*` en *Build watch paths*). Sin esto, la web publicaría `/whatsapp-bot/...`.
4. **Token permanente de Meta** (guía, sección F).

## Pendientes y decisiones abiertas

- Confirmar el número de derivación inmobiliaria: en la web, 956 771 681 es el WhatsApp **jurídico**; el inmobiliario figura como 995 149 635 / 956 764 364.
- Producción: validar la firma `X-Hub-Signature-256`; mover la memoria de conversaciones a Redis/Postgres; agrupar mensajes seguidos; servidor con dominio propio en lugar de ngrok; número de WhatsApp real de la empresa.
- Sincronización web → base de conocimiento: hoy es manual (si cambia `index.html`, actualizar la sección del prompt).
- El repositorio es público: `system_prompt.md` es visible; las credenciales no (`.env` está en `.gitignore`).
