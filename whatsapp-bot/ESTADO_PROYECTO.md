# Estado del proyecto – Valeria, asistente de WhatsApp de J&S

*Actualizado: 16/10/2026 (entorno local, ngrok y verificación GET listos).* Este archivo resume el contexto del proyecto para retomarlo en cualquier sesión.

## Resumen

- **Qué es:** asistente virtual de WhatsApp **"Valeria"** para **JS CONSULTORES Y ABOGADOS** (en la web: *J&S – Estudio Jurídico y Grupo Inmobiliario*, Dra. Yudith Sandoval, Supe/Barranca, Perú).
- **Arquitectura actual (pruebas):** WhatsApp Cloud API (Meta) ⇄ ngrok ⇄ **n8n** (Docker, puerto 5678) ⇄ **Ollama** con **`llama3`** en el mismo equipo.
- **Repositorio:** `Pantoja18/jys-grupo-web` (público), carpeta `whatsapp-bot/`, rama **`whatsapp-bot`**. La web (HTML estático en Cloudflare Pages) está en la raíz y es la fuente de verdad de la base de conocimiento.

## Hecho y verificado

| Elemento | Estado |
|---|---|
| n8n en Docker (contenedor `js-consultores-n8n`, volumen `n8n_data`) | ✅ Estable |
| `N8N_ENCRYPTION_KEY` | ✅ El `.env` usa la misma clave que el volumen `n8n_data`. **No cambiarla** (n8n dejaría de leer sus credenciales) |
| `WEBHOOK_VERIFY_TOKEN` | ✅ Definido en el `.env` |
| ngrok (plan Free) | ✅ Dominio fijo `https://feisty-papaya-feast.ngrok-free.dev`, se inicia con `ngrok http 5678` (sin dominio personalizado) |
| `WEBHOOK_URL` | ✅ `https://feisty-papaya-feast.ngrok-free.dev/` |
| Flujo n8n | ✅ Importado y publicado como **"v1 - Ollama local"** |
| Verificación GET (`hub.challenge=12345` por ngrok) | ✅ Responde `12345` |
| n8n → Ollama (`llama3:latest`, `host.docker.internal:11434`) | ✅ Conecta |
| Asistente de n8n | Desactivado |
| Clave de Anthropic | Eliminada; el bot usa solo Ollama |

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

## Próximos pasos (en este orden)

1. **Credenciales de Meta:** crear la app en Meta Developers y completar `META_ACCESS_TOKEN`, `PHONE_NUMBER_ID` y `META_API_VERSION` en el `.env`; luego `docker compose up -d --force-recreate` (guía, secciones A y B).
2. **Webhook en Meta:** URL `https://feisty-papaya-feast.ngrok-free.dev/webhook/whatsapp`, token = `WEBHOOK_VERIFY_TOKEN`, y suscribir el campo `messages` (guía, sección D.3–D.4).
3. **Prueba de extremo a extremo** por WhatsApp con la tabla de casos críticos (guía, sección E).
4. **Guardar las citas en Google Sheets:** reemplazar el nodo "Notificar al estudio (cita)" por un nodo de Google Sheets. Hoy `FIRM_NOTIFICATION_WEBHOOK_URL` está vacío y **las citas se pierden**. Tras editar el flujo hay que reimportarlo y volver a publicarlo en n8n.

Después: Cloudflare Pages (*Build command* `bash build.sh`, *Build output directory* `dist`) antes de unir la rama a `main`, y token permanente de Meta (guía, sección F).

## Pendientes y decisiones abiertas

- Confirmar el número de derivación inmobiliaria: en la web, 956 771 681 es el WhatsApp **jurídico**; el inmobiliario figura como 995 149 635 / 956 764 364.
- Producción: validar la firma `X-Hub-Signature-256`; mover la memoria de conversaciones a Redis/Postgres; agrupar mensajes seguidos; servidor con dominio propio en lugar de ngrok; número de WhatsApp real de la empresa.
- Sincronización web → base de conocimiento: hoy es manual (si cambia `index.html`, actualizar la sección del prompt).
- El repositorio es público: `system_prompt.md` es visible; las credenciales no (`.env` está en `.gitignore`).
