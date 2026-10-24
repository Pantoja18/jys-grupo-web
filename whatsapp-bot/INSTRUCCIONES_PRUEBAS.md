# Pruebas locales – Valeria, asistente de WhatsApp de JS CONSULTORES Y ABOGADOS

**Requisitos (una sola vez):** Docker Desktop, [Ollama](https://ollama.com) con el modelo descargado (`ollama pull llama3`), ngrok con su cuenta vinculada (`ngrok config add-authtoken <SU_TOKEN>`) y una app en Meta for Developers con el producto WhatsApp.

> Guía detallada de Meta y ngrok (obtener tokens, dominio fijo, pruebas y errores): **`GUIA_META_NGROK.md`**.

## Paso 1 – Configurar `.env`

En la carpeta del proyecto:
```bash
cp .env.example .env
```
Complete las variables obligatorias: `OLLAMA_URL`, `OLLAMA_MODEL`, `META_ACCESS_TOKEN`, `PHONE_NUMBER_ID`, `WEBHOOK_VERIFY_TOKEN` y `N8N_ENCRYPTION_KEY`. `WEBHOOK_URL` se completa en el paso 3.

Para el flujo v3 complete también (ver `README.md` → *Citas*):
- `POLICY_VERSION=2026-10`
- `CITAS_FILE_KEY=` con una clave generada en PowerShell (cópiela también a su gestor de contraseñas):
  ```powershell
  $b = New-Object byte[] 32; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b); -join ($b | ForEach-Object { $_.ToString('x2') })
  ```
- `TEST_NUMBERS=` su número de pruebas (para `/reiniciar`).
- `FIRM_NOTIFY_WHATSAPP=` (opcional) el número del estudio que recibirá los avisos; con el número de prueba de Meta debe estar en la lista de destinatarios verificados y haber escrito a la línea en las últimas 24 h.
- `OLLAMA_NUM_PREDICT=200`.

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

**Repetir una prueba desde cero:** ponga su número en `TEST_NUMBERS` del `.env` (con código de país, p. ej. `51XXXXXXXXX`; varios separados por comas), aplique con `docker compose up -d --force-recreate` y escriba `/reiniciar` por WhatsApp. Valeria responde *"Conversación reiniciada."* y olvida el historial y el consentimiento de ese número. Desde otros números, `/reiniciar` se trata como un mensaje normal.

## Pasar al flujo v3.1 (consentimiento con botones y citas cifradas llevadas por n8n)

1. Agregue al `.env` las variables del Paso 1 y aplique: `docker compose up -d --force-recreate` (también monta la carpeta `datos/`).
2. En n8n: *Workflows → Import from File* → `n8n_workflow_template.json`. Se crea **"JS CONSULTORES Y ABOGADOS - Valeria v3.1 (consentimiento y citas)"**.
3. Abra el flujo anterior (v2 o v3) y **desactívelo** (solo un flujo puede atender `/webhook/whatsapp`); luego abra el v3.1 y **actívelo / publíquelo**.
4. En el v3.1, *Settings* debe mostrar *Save successful production executions: Do not save* y *Save manual executions: No* (vienen así en la plantilla).

**Batería por WhatsApp del v3** (con datos ficticios; empiece con `/reiniciar`):

| # | Envíe | Esperado |
|---|---|---|
| 1 | `Hola` | Saludo según la hora, presentación una sola vez, *escribiendo…* mientras responde |
| 2 | `Quiero una cita` | Al instante, el mensaje fijo de consentimiento con botones **Acepto / No acepto** (sin IA) |
| 3 | Pulse **No acepto** | "Entendido, no usaremos sus datos…" con los canales directos; no pide datos |
| 4 | `Quiero una cita` → **Acepto** | "Gracias, hemos registrado su autorización. ¿Me indica su nombre completo…?" |
| 5 | Nombre, tema, modalidad (botones **Presencial / Virtual**), día, correo (o **Sin correo**) | n8n pregunta un dato por mensaje, al instante y sin IA, y cierra con "Su *solicitud* de cita quedó registrada…" |
| 6 | `python herramientas/ver_citas.py --env-file .env` | La solicitud aparece en la tabla (se guarda aunque el modelo no escriba `<cita>`) |
| 6b | En medio de la cita: `¿Atienden sábados?` | Responde la IA y vuelve a preguntar el dato pendiente |
| 6c | `Quiero una cita` → `cancelar` | "Entendido, dejé sin efecto la solicitud de cita…" |
| 7 | `/reiniciar` | "Conversación reiniciada." (solo desde `TEST_NUMBERS`) |

## Comparar modelos de Ollama (Fase 0)

La batería conversacional (`pruebas/evaluar_modelos.py`) envía a Ollama los mismos mensajes que n8n, con datos ficticios, y revisa automáticamente tuteo, asesoría legal, consentimiento antes de pedir datos y el bloque `<cita>`. No usa n8n, ngrok ni WhatsApp. Requiere Python 3.9 o superior (solo la biblioteca estándar).

```powershell
ollama pull qwen2.5:7b
ollama pull llama3.1:8b
python pruebas/evaluar_modelos.py --modelos llama3:latest qwen2.5:7b llama3.1:8b
```
- Muestra una tabla con los casos superados por modelo y guarda todas las respuestas en `pruebas/resultados/` (excluida de git).
- Para repetir solo algunos casos: `--casos 4 5 6`. Para más repeticiones: `--repeticiones 3`.
- Las revisiones automáticas son aproximadas: lea las respuestas del informe antes de elegir. Luego ponga el elegido en `OLLAMA_MODEL` y ejecute `docker compose up -d --force-recreate`.

Pruebas del código (no necesitan Ollama ni n8n):
```powershell
python -m pip install cryptography        # una vez (lo usan ver_citas.py y sus pruebas)
python -m unittest discover -s pruebas -v
node pruebas/test_flujo.js                # o, sin Node: docker run --rm -v "${PWD}:/w" -w /w node:20-alpine node pruebas/test_flujo.js
```
`test_flujo_n8n.py` ejecuta los nodos del flujo con Node; si Node no está instalado, esas pruebas se omiten.

La batería `evaluar_modelos.py` habla directo con Ollama, **sin** la puerta de consentimiento de n8n: sus casos de consentimiento miden al modelo, mientras que la garantía real ya la da el flujo v3.

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
- **Respuestas lentas:** en CPU, Llama 3 puede tardar decenas de segundos por mensaje; la primera respuesta es más lenta porque carga el modelo. Use `OLLAMA_NUM_PREDICT=200` (ver README → *Velocidad*). El indicador *escribiendo…* dura como máximo 25 s.
- **No aparece "escribiendo…":** revise el error del nodo *Indicador escribiendo* en n8n; según Meta, requiere un token de sistema o de empresa. Si falla, Valeria responde igual.
- **La cita no aparece en `ver_citas.py`:** solo se guarda con consentimiento otorgado y si `CITAS_FILE_KEY` tiene 64 caracteres hex; el log de n8n (`docker compose logs n8n`) muestra `[Guardar cita cifrada] ...` con el motivo. Si no hay ese mensaje en el log, revise que esté activo el flujo **v3.1** (en el v3 el guardado dependía del bloque `<cita>` del modelo). Comprobar el montaje desde PowerShell:
  ```powershell
  docker inspect js-consultores-n8n --format '{{range .Mounts}}{{.Source}} -> {{.Destination}}{{println}}{{end}}'   # debe incluir ...\datos -> /data/datos
  docker exec js-consultores-n8n sh -c 'echo $CITAS_FILE_PATH; echo largo de la clave: ${#CITAS_FILE_KEY}; ls -la /data/datos'
  docker exec js-consultores-n8n sh -c 'touch /data/datos/.prueba && echo se puede escribir && rm /data/datos/.prueba'
  docker compose logs n8n | Select-String 'Guardar cita'
  ```
- **No llega el aviso al estudio por WhatsApp:** fuera de la ventana de 24 h Meta no entrega mensajes libres, y el número de prueba solo envía a destinatarios verificados.
- **Cambios en `system_prompt.md`:** se aplican en el siguiente mensaje, sin reiniciar.
