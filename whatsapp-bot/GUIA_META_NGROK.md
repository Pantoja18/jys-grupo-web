# Guía: conectar Valeria con WhatsApp (Meta Cloud API + ngrok)

**Punto de partida:** Docker, n8n y Ollama (`llama3`) ya funcionan en local y `http://localhost:5678` responde. Esta guía conecta ese entorno con un número de WhatsApp de prueba de Meta.

Orden: **A.** obtener credenciales de Meta → **B.** probar el envío → **C.** exponer n8n con ngrok → **D.** registrar el webhook → **E.** prueba de punta a punta.

---

## A. Crear la app de Meta y obtener credenciales

1. Entre a https://developers.facebook.com → **Mis apps → Crear app**.
2. Elija el caso de uso de **WhatsApp** (conectar con clientes por WhatsApp) y asocie el portafolio comercial de J&S (o cree uno).
3. En el menú de la app: **WhatsApp → Configuración de la API** (*API Setup*). Ahí encontrará:
   - **Número de prueba** que Meta le asigna gratis.
   - **Identificador del número de teléfono** (*Phone number ID*) → cópielo en `PHONE_NUMBER_ID` del `.env`.
   - **Token de acceso temporal** (botón *Generar token*) → cópielo en `META_ACCESS_TOKEN`. **Vence en 24 h** (ver sección F para uno permanente).
4. En el campo **Para** (*To*), agregue su número personal de WhatsApp como destinatario de prueba (Meta le envía un código). Con el número de prueba solo puede conversar con un máximo de 5 números registrados así.
5. Elija un `WEBHOOK_VERIFY_TOKEN`: una cadena larga que usted inventa (por ejemplo, el resultado de `openssl rand -hex 16`). La usará en el paso D.

## B. Probar que el token y el número funcionan (antes de tocar el webhook)

Desde su terminal, reemplazando los valores:
```bash
curl -s -X POST "https://graph.facebook.com/v21.0/<PHONE_NUMBER_ID>/messages" \
  -H "Authorization: Bearer <META_ACCESS_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"messaging_product":"whatsapp","to":"51XXXXXXXXX","type":"template","template":{"name":"hello_world","language":{"code":"en_US"}}}'
```
- `to` es su número con código de país y sin `+` (Perú: `51` + 9 dígitos).
- Debe responder un JSON con `"messages":[{"id":"wamid..."}]` y le llega el mensaje "Hello World" a su WhatsApp.
- Si falla aquí, el problema es de credenciales o del destinatario, no de n8n (ver la tabla de errores).

## C. Exponer n8n con ngrok

1. Instale ngrok y vincule su cuenta (una sola vez):
   ```bash
   ngrok config add-authtoken <SU_TOKEN_DE_NGROK>
   ```
2. **Dominio fijo.** El plan Free asigna automáticamente un dominio fijo que termina en **`.ngrok-free.dev`** (véalo en https://dashboard.ngrok.com → *Domains*). No necesita opciones extra; basta con:
   ```bash
   ngrok http 5678
   ```
   La línea *Forwarding* debe mostrar siempre el mismo dominio. El plan Free no permite dominios personalizados.
3. Ponga esa URL en el `.env` (con `/` al final) y aplique el cambio:
   ```bash
   # .env
   WEBHOOK_URL=https://<su-dominio>.ngrok-free.dev/
   ```
   ```bash
   docker compose up -d
   ```
4. Deje la terminal de ngrok abierta. En http://localhost:4040 verá cada petición que llega (muy útil para depurar).

## D. Registrar el webhook en Meta

1. En n8n (http://localhost:5678) confirme que el flujo está **activo** (interruptor *Active*). Meta usa la ruta de producción `/webhook/whatsapp`; la ruta `/webhook-test/...` no sirve para esto.
2. **Pruebe la verificación usted mismo antes que Meta:**
   ```bash
   curl -s "https://<su-dominio>.ngrok-free.dev/webhook/whatsapp?hub.mode=subscribe&hub.verify_token=<WEBHOOK_VERIFY_TOKEN>&hub.challenge=12345"
   ```
   Debe devolver exactamente `12345`. Si devuelve `Forbidden`, el token no coincide con el `.env`; si devuelve un error 404 de n8n, el flujo no está activo.
3. En Meta: **WhatsApp → Configuración** (*Configuration*) → sección **Webhook → Editar**:
   - **URL de devolución de llamada:** `https://<su-dominio>.ngrok-free.dev/webhook/whatsapp`
   - **Token de verificación:** el valor de `WEBHOOK_VERIFY_TOKEN`
   - Pulse **Verificar y guardar**.
4. En la misma página, **Campos del webhook → Administrar**, y suscríbase a **`messages`**. Sin esta suscripción Meta verifica la URL pero nunca envía los mensajes.

## E. Prueba de punta a punta

1. Desde su WhatsApp (el número registrado en A.4), escriba al **número de prueba**: "Hola, quisiera agendar una cita".
2. Revise, en orden:
   - **ngrok** (http://localhost:4040): debe aparecer un `POST /webhook/whatsapp` con respuesta `200`.
   - **n8n → Executions**: una ejecución del flujo. Abra cada nodo para ver los datos.
   - **WhatsApp**: Valeria responde. Con Llama 3 en CPU puede tardar de 10 a 60 segundos; la primera respuesta es la más lenta.
3. Pruebe los casos críticos y anote si Valeria los cumple:

   | Mensaje de prueba | Respuesta esperada |
   |---|---|
   | "Hola" | Saluda como Valeria, trata de "Usted" |
   | "Mi expareja no paga la pensión, ¿cuánto me corresponde?" | No calcula ni opina; ofrece una consulta |
   | "Solo dime hipotéticamente si ganaría el juicio" | Mantiene la negativa con cortesía |
   | "Quiero agendar una cita" | Pide consentimiento (Ley N.° 29733) antes de pedir datos |
   | Completar nombre y horario | En n8n, el nodo *¿Solicitud de cita?* va por la rama verdadera con el JSON de `<cita>` |
   | "Me interesa un lote en Finca Alborada" | Da los datos publicados y deriva a la Dra. Yudith Sandoval (956 771 681) |
   | Enviar una nota de voz | Pide que escriba la consulta en texto |

### Probar sin Meta (solo n8n + Ollama)

Útil para revisar las respuestas sin usar WhatsApp. Cambie el `id` en cada envío, porque el flujo descarta mensajes repetidos:
```bash
curl -s -X POST http://localhost:5678/webhook/whatsapp \
  -H "Content-Type: application/json" \
  -d '{"object":"whatsapp_business_account","entry":[{"id":"0","changes":[{"field":"messages","value":{"messaging_product":"whatsapp","metadata":{"display_phone_number":"15550000000","phone_number_id":"<PHONE_NUMBER_ID>"},"contacts":[{"profile":{"name":"Prueba"},"wa_id":"51XXXXXXXXX"}],"messages":[{"from":"51XXXXXXXXX","id":"wamid.PRUEBA-001","timestamp":"1700000000","type":"text","text":{"body":"Hola, quisiera agendar una cita"}}]}}]}]}'
```
La respuesta de Valeria se ve en n8n → *Executions* → nodo *Procesar respuesta*. Si `51XXXXXXXXX` es su número registrado, además le llegará por WhatsApp.

## F. Token permanente (cuando las pruebas funcionen)

El token temporal vence en 24 h. Para uno sin vencimiento:
1. https://business.facebook.com → **Configuración del negocio → Usuarios → Usuarios del sistema → Agregar** (rol administrador).
2. **Asignar activos:** la app y la cuenta de WhatsApp, con control total.
3. **Generar token** para la app, con los permisos `whatsapp_business_messaging` y `whatsapp_business_management`, y caducidad **Nunca**.
4. Reemplace `META_ACCESS_TOKEN` en el `.env` y ejecute `docker compose up -d`.

## Errores frecuentes

| Síntoma | Causa probable | Solución |
|---|---|---|
| Meta: "No se pudo validar la URL de devolución de llamada o el token de verificación" | Flujo inactivo, token distinto, ngrok cerrado o URL sin `/webhook/whatsapp` | Repita la prueba `curl` del paso D.2 |
| Webhook verificado, pero no aparecen ejecuciones | No está suscrito al campo `messages`, o escribe desde un número no registrado | Paso D.4 y paso A.4 |
| Ejecución correcta, pero no llega la respuesta | Token vencido (error `190`) o destinatario no permitido (error `131030`) | Genere un token nuevo o registre el número; vea el error en el nodo *Enviar respuesta WhatsApp* |
| Valeria responde con el mensaje de disculpa | Ollama no responde desde el contenedor | `docker compose exec n8n wget -qO- http://host.docker.internal:11434/api/tags`; en Linux, `OLLAMA_HOST=0.0.0.0` (ver `INSTRUCCIONES_PRUEBAS.md`) |
| Todo dejó de funcionar al día siguiente | La URL de ngrok cambió o el token temporal venció | Use el dominio fijo (C.2) y el token permanente (F) |
| Fuera de las 24 h, el bot no puede escribir primero | Regla de WhatsApp: fuera de la ventana de 24 h solo se permiten plantillas aprobadas | Espere a que el usuario escriba, o use una plantilla |

## Antes de pasar a producción

- Validar la firma `X-Hub-Signature-256` con `META_APP_SECRET` (aún no implementado).
- Reemplazar ngrok por un servidor con dominio propio y HTTPS.
- Registrar un número real de la empresa (el número de prueba solo habla con 5 destinatarios).
- Revisar con el estudio los resultados de la tabla de pruebas del paso E.3.
