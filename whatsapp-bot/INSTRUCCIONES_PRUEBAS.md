# Pruebas locales – Valeria, asistente de WhatsApp de JS CONSULTORES Y ABOGADOS

**Requisitos (una sola vez):** Docker Desktop, [Ollama](https://ollama.com) con el modelo descargado (`ollama pull llama3`), ngrok con su cuenta vinculada (`ngrok config add-authtoken <SU_TOKEN>`) y una app en Meta for Developers con el producto WhatsApp.

> Guía detallada de Meta y ngrok (obtener tokens, dominio fijo, pruebas y errores): **`GUIA_META_NGROK.md`**.

## Paso 1 – Configurar `.env`

En la carpeta del proyecto:
```bash
cp .env.example .env
```
Complete las variables obligatorias: `OLLAMA_URL`, `OLLAMA_MODEL`, `META_ACCESS_TOKEN`, `PHONE_NUMBER_ID`, `WEBHOOK_VERIFY_TOKEN` y `N8N_ENCRYPTION_KEY`. `WEBHOOK_URL` se completa en el paso 3.

## Paso 2 – Iniciar n8n

Verifique primero que Ollama responde (en el equipo anfitrión):
```bash
curl -s http://localhost:11434/api/tags      # debe listar "llama3"
```
**En Linux**, Ollama escucha solo en `127.0.0.1` y el contenedor no lo alcanza. Configúrelo para escuchar en todas las interfaces (`sudo systemctl edit ollama` → `Environment="OLLAMA_HOST=0.0.0.0"`, luego `sudo systemctl restart ollama`), y no exponga el puerto 11434 a internet. En Docker Desktop (Windows/macOS) no hace falta.


```bash
docker compose up -d
```
Abra http://localhost:5678, cree su usuario, importe el flujo (*Workflows → Import from File → `n8n_workflow_template.json`*) y **actívelo** (interruptor *Active*). Meta llama a la URL de producción `/webhook/...`, que solo responde con el flujo activo.

## Paso 3 – Exponer el puerto con ngrok

En otra terminal (déjela abierta):
```bash
ngrok http 5678
```
Copie la URL HTTPS de la línea `Forwarding` (o ejecute `curl -s http://localhost:4040/api/tunnels | grep -o 'https://[^"]*'`). Póngala en `WEBHOOK_URL` dentro de `.env` y aplique el cambio:
```bash
docker compose up -d
```

## Paso 4 – Configurar el webhook en Meta

En **Meta for Developers → su app → WhatsApp → Configuración → Webhook → Editar**:

- **URL de devolución de llamada:** `https://<tu-url-ngrok>/webhook/whatsapp`
- **Token de verificación:** el mismo valor de `WEBHOOK_VERIFY_TOKEN`
- Pulse **Verificar y guardar** y, en *Campos del webhook*, suscríbase a **`messages`**.

Envíe un WhatsApp al número de prueba desde un número registrado como destinatario y revise el resultado en n8n → *Executions*.

## Verificar el estado de Docker

```bash
docker compose ps                    # estado del contenedor (debe decir "running")
docker compose logs -f n8n           # logs en vivo (Ctrl+C para salir)
curl -s http://localhost:5678/healthz   # debe responder {"status":"ok"}
docker volume inspect n8n_data   # volumen persistente
docker compose exec n8n wget -qO- http://host.docker.internal:11434/api/tags   # n8n alcanza a Ollama
```
Para detener: `docker compose down` (los datos se conservan en el volumen). Para reiniciar tras cambiar `.env`: `docker compose up -d`.

## Problemas frecuentes

- **Meta no verifica el webhook:** el flujo no está activo, el token no coincide, o la URL no termina en `/webhook/whatsapp`.
- **La URL de ngrok cambió** (plan gratuito, al reiniciar ngrok): repita los pasos 3 y 4.
- **No llegan respuestas:** el token temporal de Meta vence en 24 h; revise también que su número esté como destinatario de prueba.
- **Valeria responde con el mensaje de disculpa:** Ollama no está corriendo, el contenedor no lo alcanza (ver Paso 2) o falta `ollama pull llama3`. Revise el error del nodo *Ollama (chat local)* en n8n → *Executions*.
- **Respuestas lentas:** en CPU, Llama 3 puede tardar decenas de segundos por mensaje; la primera respuesta es más lenta porque carga el modelo.
- **Cambios en `system_prompt.md`:** se aplican en el siguiente mensaje, sin reiniciar.
