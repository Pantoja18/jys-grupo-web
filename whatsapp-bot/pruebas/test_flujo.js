// Pruebas del flujo de n8n (n8n_workflow_template.json) sin n8n: ejecuta el código de los nodos
// "Code" con objetos simulados ($input, $env, $getWorkflowStaticData) y revisa la estructura del flujo.
//
// Uso, desde whatsapp-bot/:
//   node pruebas/test_flujo.js
// Sin Node instalado (PowerShell, con Docker):
//   docker run --rm -v "${PWD}:/w" -w /w node:20-alpine node pruebas/test_flujo.js

'use strict';
const assert = require('assert');
const { wf, nodes, runCode, evalParam } = require('./nodos_n8n');

const INPUT = { from: '51900000000', messageId: 'wamid.T1', contactName: 'Prueba', phoneNumberId: '100', text: 'Hola', buttonId: '', needsAI: true, directReply: '' };
const POLICY = { POLICY_VERSION: '2026-10' };

// Puerta de consentimiento → Estado de la cita → (si va a la IA) Preparar solicitud LLM.
function gate(input = {}, store = {}, env = {}) {
  return runCode('Puerta de consentimiento', { input: { ...INPUT, ...input }, env: { ...POLICY, ...env }, store })[0].json;
}

function route(input = {}, store = {}, env = {}) {
  return runCode('Estado de la cita', { input: gate(input, store, env), env: { ...POLICY, ...env }, store })[0].json;
}

function prepare(env = {}, store = {}, input = {}) {
  const g = route(input, store, env);
  assert.strictEqual(g.action, 'llm', `el mensaje no fue a la IA: ${g.reply}`);
  return runCode('Preparar solicitud LLM', { input: { success: true }, env: { ...POLICY, ...env }, store, refs: { 'Estado de la cita': g } })[0].json;
}

// Último nodo antes de enviar: guarda la cita (si hay) y ajusta la respuesta.
function finish(json, store, env = {}) {
  return runCode('Guardar cita cifrada', { input: json, env: { ...POLICY, ...env }, store })[0].json;
}

const granted = () => ({ consents: { [INPUT.from]: { estado: 'otorgado', fecha: '2026-10-24T12:00:00.000Z', version: '2026-10', canal: 'whatsapp_botones' } } });

function parse(response, provider = 'ollama', store = granted(), input = {}) {
  const ctx = prepare({ LLM_PROVIDER: provider }, store, input);
  return { out: runCode('Procesar respuesta', { input: response, store, refs: { 'Preparar solicitud LLM': ctx } })[0].json, store };
}

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

// ---------- Estructura del flujo ----------

test('las conexiones apuntan a nodos que existen', () => {
  for (const [src, conn] of Object.entries(wf.connections)) {
    assert.ok(nodes[src], `origen inexistente: ${src}`);
    for (const out of conn.main) for (const c of out) assert.ok(nodes[c.node], `destino inexistente: ${c.node}`);
  }
});

test("las referencias $('...') del código apuntan a nodos que existen", () => {
  for (const n of wf.nodes.filter((x) => x.parameters && x.parameters.jsCode)) {
    for (const m of n.parameters.jsCode.matchAll(/\$\('([^']+)'\)/g)) assert.ok(nodes[m[1]], `${n.name} usa $('${m[1]}')`);
  }
});

test('el selector de proveedor envía "anthropic" a Claude y lo demás a Ollama', () => {
  const cond = nodes['¿Usar Claude?'].parameters.conditions.conditions[0];
  assert.strictEqual(cond.leftValue, '={{ $json.provider }}');
  assert.strictEqual(cond.rightValue, 'anthropic');
  const outs = wf.connections['¿Usar Claude?'].main;
  assert.strictEqual(outs[0][0].node, 'Claude (Messages API)');
  assert.strictEqual(outs[1][0].node, 'Ollama (chat local)');
  assert.strictEqual(wf.connections['Preparar solicitud LLM'].main[0][0].node, '¿Usar Claude?');
  for (const n of ['Claude (Messages API)', 'Ollama (chat local)']) {
    assert.strictEqual(wf.connections[n].main[0][0].node, 'Procesar respuesta');
    assert.strictEqual(nodes[n].parameters.jsonBody, '={{ JSON.stringify($json.llmBody) }}');
    assert.strictEqual(nodes[n].onError, 'continueRegularOutput');
  }
});

test('no se guardan ejecuciones exitosas ni manuales', () => {
  assert.strictEqual(wf.settings.saveDataSuccessExecution, 'none');
  assert.strictEqual(wf.settings.saveManualExecutions, false);
});

test('el flujo no contiene claves ni tokens escritos a mano', () => {
  const text = JSON.stringify(wf);
  assert.ok(!/sk-ant-[A-Za-z0-9]/.test(text), 'clave de Anthropic en el flujo');
  assert.ok(!/EAA[A-Za-z0-9]{20,}/.test(text), 'token de Meta en el flujo');
});

// ---------- Preparar solicitud LLM ----------

test('Ollama por defecto: system prompt completo (con recordatorio) y contexto de 8192', () => {
  const ctx = prepare();
  assert.strictEqual(ctx.provider, 'ollama');
  const b = ctx.llmBody;
  assert.strictEqual(b.model, 'llama3:latest');
  assert.strictEqual(b.stream, false);
  assert.strictEqual(b.options.num_ctx, 8192);
  assert.strictEqual(b.options.num_predict, 200);
  assert.strictEqual(b.messages[0].role, 'system');
  assert.ok(b.messages[0].content.includes('## Recordatorio final'));
  assert.ok(b.messages[0].content.includes('## Trato de "Usted"'));
  assert.strictEqual(b.messages.at(-1).role, 'user');
  assert.ok(b.messages.at(-1).content.endsWith('Hola'));
});

test('OLLAMA_MODEL cambia el modelo', () => {
  assert.strictEqual(prepare({ OLLAMA_MODEL: 'qwen2.5:7b' }).llmBody.model, 'qwen2.5:7b');
});

test('Anthropic: system con caché, mensajes sin rol system, thinking adaptativo y fallbacks', () => {
  const ctx = prepare({ LLM_PROVIDER: 'Anthropic ' });
  assert.strictEqual(ctx.provider, 'anthropic');
  const b = ctx.llmBody;
  assert.strictEqual(b.model, 'claude-sonnet-5-5');
  assert.deepStrictEqual(b.thinking, { type: 'adaptive' });
  assert.strictEqual(b.fallbacks, 'default');
  assert.strictEqual(b.system[0].cache_control.type, 'ephemeral');
  assert.ok(b.system[0].text.includes('## Recordatorio final'));
  assert.ok(b.messages.every((m) => m.role !== 'system'));
  assert.strictEqual(b.messages[0].role, 'user');
  assert.ok(!('temperature' in b) && !('options' in b));
});

test('LLM_PROVIDER no válido detiene el flujo con un error claro', () => {
  assert.throws(() => prepare({ LLM_PROVIDER: 'openai' }), /LLM_PROVIDER no válido/);
});

test('el historial guardado se envía antes del turno actual', () => {
  const store = { conversations: { [INPUT.from]: { updatedAt: Date.now(), messages: [
    { role: 'user', content: 'Hola' }, { role: 'assistant', content: 'Buenos días, ¿en qué puedo ayudarle?' },
  ] } } };
  const msgs = prepare({}, store).llmBody.messages;
  assert.deepStrictEqual(msgs.map((m) => m.role), ['system', 'user', 'assistant', 'user']);
});

// ---------- Procesar respuesta ----------

const CITA = '{"nombre":"María Pérez Ríos","area":"Procesos de alimentos","resumen":"x","tipo_cliente":"persona_natural","modalidad":"presencial","preferencia_horaria":"martes","correo":"","urgente":false,"contacto_humano":false,"estado":"completa"}';

test('Ollama: extrae <cita>, la oculta y guarda el turno', () => {
  const { out, store } = parse({ message: { role: 'assistant', content: `Gracias, Sra. Pérez.\n<cita>${CITA}</cita>` } });
  assert.strictEqual(out.reply, 'Gracias, Sra. Pérez.');
  assert.strictEqual(out.hasAppointment, true);
  assert.strictEqual(out.appointment.nombre, 'María Pérez Ríos');
  assert.strictEqual(store.conversations[INPUT.from].messages.at(-1).content, 'Gracias, Sra. Pérez.');
});

test('Anthropic: usa solo los bloques de texto y tolera la cita dentro de ```', () => {
  const { out } = parse({ stop_reason: 'end_turn', usage: { input_tokens: 10, output_tokens: 5 }, content: [
    { type: 'thinking', thinking: '' },
    { type: 'text', text: `Con gusto le ayudo.\n\`\`\`json\n<cita>${CITA}</cita>\n\`\`\`` },
  ] }, 'anthropic');
  assert.strictEqual(out.reply, 'Con gusto le ayudo.');
  assert.strictEqual(out.appointment.modalidad, 'presencial');
  assert.strictEqual(out.provider, 'anthropic');
  assert.strictEqual(out.usage.input_tokens, 10);
});

test('Anthropic: un rechazo (refusal) responde con cortesía y no se guarda en el historial', () => {
  const { out, store } = parse({ stop_reason: 'refusal', content: [] }, 'anthropic');
  assert.ok(out.reply.startsWith('Disculpe, no puedo ayudarle'));
  assert.strictEqual(out.hasAppointment, false);
  assert.ok(!store.conversations || !store.conversations[INPUT.from]);
});

test('error del LLM: mensaje de cortesía y sin historial', () => {
  for (const provider of ['ollama', 'anthropic']) {
    const { out, store } = parse({ error: { message: 'connect ECONNREFUSED' } }, provider);
    assert.ok(out.reply.startsWith('Disculpe, en este momento no puedo'));
    assert.ok(!store.conversations || !store.conversations[INPUT.from]);
  }
});

test('varios bloques <cita>: se usa el último válido y no queda ninguno visible', () => {
  const second = CITA.replace('martes', 'jueves');
  const { out } = parse({ message: { content: `Hola <cita>${CITA}</cita> texto <cita>${second}</cita> <cita>{roto</cita>` } });
  assert.strictEqual(out.appointment.preferencia_horaria, 'jueves');
  assert.ok(!out.reply.includes('cita>'), out.reply);
});

test('bloque <cita> sin cerrar: no se muestra al usuario', () => {
  const { out } = parse({ message: { content: 'Perfecto, la registro. <cita>{"nombre":"Mar' } });
  assert.strictEqual(out.reply, 'Perfecto, la registro.');
  assert.strictEqual(out.hasAppointment, false);
});

test('respuesta que solo trae el bloque <cita>: confirma el registro', () => {
  const { out } = parse({ message: { content: `<cita>${CITA}</cita>` } });
  assert.ok(out.reply.startsWith('Gracias, he registrado su solicitud'));
  assert.strictEqual(out.hasAppointment, true);
});

test('respuesta vacía de Ollama: mensaje de cortesía', () => {
  const { out } = parse({ message: { content: '   ' } });
  assert.ok(out.reply.startsWith('Disculpe, en este momento no puedo'));
});

// ---------- Puerta de consentimiento (Ley N.° 29733) ----------

const CONSENT_TEXT = 'Para gestionar su cita necesitamos usar algunos datos personales (nombre, horario preferido y, opcionalmente, su correo), conforme a la Ley N.° 29733. Puede revisar nuestra política en construccionesyedificacionesjys.com/terminos.html#privacidad. ¿Nos autoriza?';

test('la puerta va antes de la IA y las respuestas fijas no pasan por el LLM', () => {
  assert.strictEqual(wf.connections['Extraer mensaje'].main[0][0].node, 'Puerta de consentimiento');
  assert.strictEqual(wf.connections['Puerta de consentimiento'].main[0][0].node, 'Estado de la cita');
  assert.strictEqual(wf.connections['Estado de la cita'].main[0][0].node, '¿Llamar a la IA?');
  const cond = nodes['¿Llamar a la IA?'].parameters.conditions.conditions[0];
  assert.strictEqual(cond.leftValue, '={{ $json.action }}');
  assert.strictEqual(cond.rightValue, 'llm');
  const outs = wf.connections['¿Llamar a la IA?'].main;
  assert.strictEqual(outs[1][0].node, 'Guardar cita cifrada');
  assert.ok(nodes['Enviar respuesta WhatsApp'].parameters.jsonBody.includes('...$json.waMessage'));
});

test('el texto fijo de consentimiento es igual en la puerta y en system_prompt.md', () => {
  const prompt = require('fs').readFileSync(require('./nodos_n8n').PROMPT_PATH, 'utf8');
  assert.ok(prompt.includes(CONSENT_TEXT));
  assert.strictEqual(gate({ text: 'Quiero una cita' }).consentText, CONSENT_TEXT);
});

test('sin consentimiento, pedir una cita no llama a la IA: envía los botones Acepto / No acepto', () => {
  for (const text of ['Quiero agendar una cita', 'Necesito hablar con un abogado', 'quiero hablar con una persona', 'Me llamo Ana Torres', 'mi correo es ana@example.com', 'Mi DNI es 12345678']) {
    const store = {};
    const g = gate({ text }, store);
    assert.strictEqual(g.action, 'reply', text);
    assert.strictEqual(g.waMessage.type, 'interactive');
    assert.strictEqual(g.waMessage.interactive.body.text, CONSENT_TEXT);
    assert.deepStrictEqual(g.waMessage.interactive.action.buttons.map((b) => [b.reply.id, b.reply.title]), [['consent_si', 'Acepto'], ['consent_no', 'No acepto']]);
    assert.strictEqual(store.consents[INPUT.from].prompted, true);
  }
});

test('los datos personales enviados antes del consentimiento no se guardan en el historial', () => {
  const store = {};
  gate({ text: 'Soy Ana, mi correo es ana@example.com y quiero una cita' }, store);
  const saved = JSON.stringify(store.conversations);
  assert.ok(!saved.includes('ana@example.com'), saved);
});

test('un saludo o una pregunta general sin intención de cita sí va a la IA', () => {
  for (const text of ['Hola', '¿Dónde queda su oficina?', '¿Qué servicios ofrecen?']) assert.strictEqual(gate({ text }).action, 'llm', text);
});

test('botón "Acepto": registra otorgado con fecha y versión, y responde sin la IA', () => {
  const store = {};
  gate({ text: 'Quiero una cita' }, store);
  const g = gate({ text: 'Acepto', buttonId: 'consent_si', messageId: 'wamid.T2' }, store);
  assert.strictEqual(g.action, 'reply');
  assert.ok(g.reply.startsWith('Gracias, hemos registrado su autorización'));
  const c = store.consents[INPUT.from];
  assert.strictEqual(c.estado, 'otorgado');
  assert.strictEqual(c.version, '2026-10');
  assert.strictEqual(c.canal, 'whatsapp_botones');
  assert.ok(!Number.isNaN(Date.parse(c.fecha)));
  // Con consentimiento, la siguiente petición de cita ya va a la IA.
  assert.strictEqual(gate({ text: 'Quiero una cita el martes' }, store).action, 'llm');
  assert.strictEqual(gate({ text: 'Quiero una cita el martes' }, store).consentStatus, 'otorgado');
});

test('consentimiento por texto: "acepto", "Sí, acepto" y "sí" justo después de preguntar', () => {
  for (const text of ['acepto', 'Sí, acepto.', 'ACEPTO']) {
    const store = {};
    assert.strictEqual(gate({ text }, store).action, 'reply', text);
    assert.strictEqual(store.consents[INPUT.from].estado, 'otorgado', text);
    assert.strictEqual(store.consents[INPUT.from].canal, 'whatsapp_texto');
  }
  const store = {};
  gate({ text: 'Quiero una cita' }, store);
  gate({ text: 'sí' }, store);
  assert.strictEqual(store.consents[INPUT.from].estado, 'otorgado');
  // Un "sí" suelto sin pregunta previa no cuenta como consentimiento.
  const other = {};
  assert.strictEqual(gate({ text: 'sí' }, other).action, 'llm');
  assert.ok(!other.consents[INPUT.from]);
});

test('rechazo ("No acepto"): registra rechazado, no pide datos y da los canales directos', () => {
  for (const [text, buttonId] of [['No acepto', 'consent_no'], ['no acepto', '']]) {
    const store = {};
    gate({ text: 'Quiero una cita' }, store);
    const g = gate({ text, buttonId }, store);
    assert.strictEqual(store.consents[INPUT.from].estado, 'rechazado');
    assert.ok(g.reply.includes('no usaremos sus datos personales'));
    assert.ok(g.reply.includes('956 771 681') && g.reply.includes('993 793 224'));
    assert.ok(!/nombre|indíqueme/i.test(g.reply), g.reply);
    // Las preguntas generales siguen yendo a la IA, que recibe "Consentimiento: rechazado".
    const ctx = prepare({}, store, { text: '¿Qué servicios tienen?' });
    assert.ok(ctx.llmBody.messages[0].content.trimEnd().endsWith('Consentimiento: rechazado'));
  }
});

test('un consentimiento de una versión anterior de la política se vuelve a pedir', () => {
  const store = granted();
  assert.strictEqual(gate({ text: 'Quiero una cita' }, store, { POLICY_VERSION: '2027-01' }).action, 'reply');
});

test('la IA recibe "Consentimiento: ..." al final del recordatorio (Ollama y Claude)', () => {
  const o = prepare({}, granted()).llmBody.messages[0].content;
  assert.ok(o.includes('## Recordatorio final'));
  assert.ok(o.trimEnd().endsWith('Consentimiento: otorgado'));
  assert.ok(prepare({}, {}).llmBody.messages[0].content.trimEnd().endsWith('Consentimiento: pendiente'));
  const a = prepare({ LLM_PROVIDER: 'anthropic' }, {}).llmBody.system;
  assert.strictEqual(a.length, 2);
  assert.ok(a[0].cache_control && !a[1].cache_control);
  assert.strictEqual(a[1].text, 'Consentimiento: pendiente');
});

test('filtro de salida: sin consentimiento, si la IA pide datos se envían los botones', () => {
  for (const content of ['Con gusto. ¿Me indica su nombre completo, por favor?', 'Indíqueme su correo y su DNI.', '¿Cuál es su número de teléfono?', 'Por favor, bríndeme su dirección.']) {
    const store = {};
    const { out } = parse({ message: { content } }, 'ollama', store);
    assert.strictEqual(out.blockedByConsent, true, content);
    assert.strictEqual(out.waMessage.type, 'interactive');
    assert.strictEqual(out.reply, CONSENT_TEXT);
    assert.strictEqual(store.consents[INPUT.from].prompted, true);
    assert.strictEqual(store.conversations[INPUT.from].messages.at(-1).content, CONSENT_TEXT);
  }
});

test('filtro de salida: no bloquea respuestas normales ni con consentimiento otorgado', () => {
  for (const content of ['Puede comunicarse al teléfono 993 793 224 o al correo Ejuridicojys@gmail.com.', 'Atendemos de lunes a viernes. ¿En qué puedo ayudarle?']) {
    const { out } = parse({ message: { content } }, 'ollama', {});
    assert.strictEqual(out.blockedByConsent, false, content);
    assert.strictEqual(out.waMessage.type, 'text');
  }
  const { out } = parse({ message: { content: '¿Me indica su nombre completo, por favor?' } });
  assert.strictEqual(out.blockedByConsent, false);
});

test('sin consentimiento, un bloque <cita> no se registra ni se notifica', () => {
  const { out } = parse({ message: { content: `Gracias.\n<cita>${CITA}</cita>` } }, 'ollama', {});
  assert.strictEqual(out.hasAppointment, false);
  assert.strictEqual(out.appointment, null);
  assert.strictEqual(out.reply, 'Gracias.');
});

test('mensaje sin texto: respuesta directa sin la IA', () => {
  const g = gate({ text: '', needsAI: false, directReply: 'Disculpe, no pude leer su mensaje.' });
  assert.strictEqual(g.action, 'reply');
  assert.strictEqual(g.waMessage.text.body, 'Disculpe, no pude leer su mensaje.');
});

// ---------- Guardar cita cifrada ----------

const cryptoNode = require('crypto');
const os = require('os');
const KEY = 'ab'.repeat(32);

function decryptLine(line, keyHex = KEY) {
  assert.ok(line.startsWith('v1.'));
  const raw = Buffer.from(line.slice(3), 'base64');
  const d = cryptoNode.createDecipheriv('aes-256-gcm', Buffer.from(keyHex, 'hex'), raw.subarray(0, 12));
  d.setAAD(Buffer.from('jys-citas-v1'));
  d.setAuthTag(raw.subarray(raw.length - 16));
  return JSON.parse(Buffer.concat([d.update(raw.subarray(12, raw.length - 16)), d.final()]).toString('utf8'));
}

function saveCita(appointment, { store = granted(), env = {} } = {}) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'citas-'));
  const file = path.join(dir, 'sub', 'citas.jsonl.enc');
  const input = { from: INPUT.from, phoneNumberId: '100', appointment, hasAppointment: true, consentStatus: 'otorgado', policyVersion: '2026-10', provider: 'ollama', reply: 'Gracias.', waMessage: { type: 'text', text: { body: 'Gracias.' } } };
  const run = () => runCode('Guardar cita cifrada', { input, store, env: { CITAS_FILE_KEY: KEY, CITAS_FILE_PATH: file, ...env } })[0].json;
  return { run, file, store };
}
const fs = require('fs');
const path = require('path');

test('todas las respuestas pasan por "Guardar cita cifrada" antes de enviarse', () => {
  assert.ok(!nodes['¿Solicitud de cita?'], 'el guardado ya no depende de un IF sobre <cita>');
  assert.deepStrictEqual(wf.connections['Procesar respuesta'].main[0].map((c) => c.node), ['Guardar cita cifrada']);
  assert.deepStrictEqual(wf.connections['¿Llamar a la IA?'].main[1].map((c) => c.node), ['Guardar cita cifrada']);
  assert.deepStrictEqual(wf.connections['Guardar cita cifrada'].main[0].map((c) => c.node).sort(), ['Enviar respuesta WhatsApp', '¿Avisar por WhatsApp?', '¿Webhook configurado?']);
  for (const [src, conn] of Object.entries(wf.connections)) {
    if (src !== 'Guardar cita cifrada') assert.ok(!conn.main.flat().some((c) => c.node === 'Enviar respuesta WhatsApp'), src);
  }
  assert.strictEqual(wf.connections['¿Avisar por WhatsApp?'].main[0][0].node, 'Avisar al estudio (WhatsApp)');
  assert.strictEqual(wf.connections['¿Webhook configurado?'].main[0][0].node, 'Notificar al estudio (cita)');
  for (const n of ['Avisar al estudio (WhatsApp)', 'Notificar al estudio (cita)']) assert.strictEqual(nodes[n].onError, 'continueRegularOutput');
  assert.ok(!nodes['Guardar cita cifrada'].parameters.jsCode.includes("require('path')"), 'n8n solo permite fs y crypto');
});

test('con consentimiento, <cita> válido → hasAppointment y se cifra con AES-256-GCM (cifrar y descifrar)', () => {
  const { out } = parse({ message: { content: `Gracias.\n<cita>${CITA}</cita>` } });
  assert.strictEqual(out.hasAppointment, true);
  const { run, file } = saveCita(out.appointment);
  const r = run();
  assert.strictEqual(r.saved, true);
  const lines = fs.readFileSync(file, 'utf8').trim().split('\n');
  assert.strictEqual(lines.length, 1);
  assert.ok(!lines[0].includes('María'), 'el archivo no debe contener texto plano');
  const rec = decryptLine(lines[0]);
  assert.strictEqual(rec.nombre, 'María Pérez Ríos');
  assert.strictEqual(rec.telefono, INPUT.from);
  assert.strictEqual(rec.consentimiento.version, '2026-10');
  assert.strictEqual(rec.consentimiento.estado, 'otorgado');
  assert.throws(() => decryptLine(lines[0], 'cd'.repeat(32)), 'con otra clave no se descifra');
});

test('la misma cita no se guarda dos veces; una cita actualizada sí', () => {
  const { run, file, store } = saveCita(JSON.parse(CITA));
  assert.strictEqual(run().saved, true);
  assert.strictEqual(run().duplicate, true);
  const r = runCode('Guardar cita cifrada', { input: { from: INPUT.from, hasAppointment: true, appointment: { ...JSON.parse(CITA), correo: 'm@example.com' } }, store, env: { CITAS_FILE_KEY: KEY, CITAS_FILE_PATH: file } })[0].json;
  assert.strictEqual(r.saved, true);
  assert.strictEqual(fs.readFileSync(file, 'utf8').trim().split('\n').length, 2);
});

test('aviso al estudio: una vez, sin datos sensibles, y solo si hay número o webhook', () => {
  const { run } = saveCita(JSON.parse(CITA), { env: { FIRM_NOTIFY_WHATSAPP: '+51 900 000 001' } });
  const r = run();
  assert.strictEqual(r.notifyWhatsApp, true);
  assert.strictEqual(r.notifyWebhook, false);
  assert.strictEqual(r.notifyTo, '51900000001');
  assert.strictEqual(r.notice, 'Nueva solicitud de cita – área: Procesos de alimentos – modalidad: presencial. Revise el registro.');
  for (const s of ['María', INPUT.from, 'Pensión']) assert.ok(!r.notice.includes(s), s);
  const { run: sinNumero } = saveCita(JSON.parse(CITA));
  assert.strictEqual(sinNumero().notifyWhatsApp, false);
  // Incompleta: se guarda, pero todavía no se avisa.
  const { run: incompleta } = saveCita({ ...JSON.parse(CITA), estado: 'incompleta' }, { env: { FIRM_NOTIFY_WHATSAPP: '51900000001' } });
  const i = incompleta();
  assert.strictEqual(i.saved, true);
  assert.strictEqual(i.notifyWhatsApp, false);
});

test('sin CITAS_FILE_KEY válida no se escribe nada y el aviso lo advierte', () => {
  const { run, file } = saveCita(JSON.parse(CITA), { env: { CITAS_FILE_KEY: '1234', FIRM_NOTIFY_WHATSAPP: '51900000001' } });
  const r = run();
  assert.strictEqual(r.saved, false);
  assert.ok(r.error.includes('CITAS_FILE_KEY'));
  assert.ok(!fs.existsSync(file));
  assert.ok(r.notice.includes('no se pudo guardar'));
});

// ---------- Estado de la cita (n8n lleva la solicitud, sin depender de <cita>) ----------

// Un turno completo sin IA: puerta → estado → guardar. Falla si el mensaje fuera al LLM.
function turn(text, store, { buttonId = '', env = {} } = {}) {
  const r = route({ text, buttonId }, store, env);
  assert.strictEqual(r.action, 'reply', `"${text}" fue a la IA`);
  return finish(r, store, env);
}
function citaEnv() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'flujo-'));
  return { CITAS_FILE_KEY: KEY, CITAS_FILE_PATH: path.join(dir, 'citas.jsonl.enc') };
}
const lines = (file) => (fs.existsSync(file) ? fs.readFileSync(file, 'utf8').trim().split('\n').filter(Boolean) : []);
const body = (r) => (r.waMessage.type === 'interactive' ? r.waMessage.interactive.body.text : r.waMessage.text.body);

test('flujo completo sin la IA: consentimiento → nombre → tema → modalidad → horario → correo → cita cifrada', () => {
  const env = citaEnv();
  const store = {};
  assert.strictEqual(turn('Quiero una cita', store, { env }).waMessage.type, 'interactive');
  let r = turn('Acepto', store, { buttonId: 'consent_si', env });
  assert.strictEqual(r.reply, 'Gracias, hemos registrado su autorización. ¿Me indica su nombre completo, por favor?');
  r = turn('Ana Torres Vega', store, { env });
  assert.ok(r.reply.includes('tema de su consulta'), r.reply);
  r = turn('Quiero iniciar una demanda de pensión de alimentos', store, { env });
  assert.deepStrictEqual(r.waMessage.interactive.action.buttons.map((b) => b.reply.id), ['cita_presencial', 'cita_virtual']);
  r = turn('Presencial', store, { buttonId: 'cita_presencial', env });
  assert.ok(r.reply.includes('¿Qué día y en qué horario'), r.reply);
  r = turn('El martes por la mañana', store, { env });
  assert.ok(r.reply.includes('correo'), r.reply);
  assert.strictEqual(lines(env.CITAS_FILE_PATH).length, 0, 'todavía no se guarda');
  r = turn('ana@example.com', store, { env });

  assert.strictEqual(r.saved, true);
  assert.strictEqual(r.origen, 'flujo');
  assert.ok(r.reply.startsWith('Su *solicitud* de cita quedó registrada: Ana Torres Vega'), r.reply);
  assert.ok(r.reply.includes('presencial') && r.reply.includes('El martes por la mañana'));
  const recs = lines(env.CITAS_FILE_PATH).map((l) => decryptLine(l));
  assert.strictEqual(recs.length, 1);
  assert.deepStrictEqual(
    [recs[0].nombre, recs[0].area, recs[0].modalidad, recs[0].preferencia_horaria, recs[0].correo, recs[0].estado, recs[0].origen],
    ['Ana Torres Vega', 'Procesos de alimentos', 'presencial', 'El martes por la mañana', 'ana@example.com', 'completa', 'flujo']);
  assert.strictEqual(recs[0].consentimiento.estado, 'otorgado');
  assert.strictEqual(store.citaFlow[INPUT.from].estado, 'registrada');
  // El historial guarda los turnos fijos, para que la IA conozca la conversación.
  assert.strictEqual(store.conversations[INPUT.from].messages.at(-1).content, r.reply);
  // Después del registro, lo demás vuelve a la IA, que sabe que la solicitud ya está registrada.
  const ctx = prepare({}, store, { text: '¿Dónde queda su oficina?' });
  assert.ok(ctx.llmBody.messages[0].content.includes('Solicitud de cita: registrada'));
});

test('no vuelve a pedir datos ya dados (modalidad en el primer mensaje, correo junto al tema)', () => {
  const env = citaEnv();
  const store = granted();
  let r = turn('Quiero una cita virtual', store, { env });
  assert.ok(r.reply.includes('nombre completo'));
  r = turn('Me llamo Luis Rojas', store, { env });
  r = turn('Herencia de mi padre, sin testamento. Mi correo es luis@example.com', store, { env });
  assert.ok(r.reply.includes('¿Qué día'), `no debía preguntar la modalidad: ${r.reply}`);
  r = turn('jueves en la tarde', store, { env });
  assert.strictEqual(r.saved, true, `no debía preguntar el correo: ${r.reply}`);
  const rec = decryptLine(lines(env.CITAS_FILE_PATH)[0]);
  assert.deepStrictEqual([rec.nombre, rec.modalidad, rec.correo, rec.area], ['Luis Rojas', 'virtual', 'luis@example.com', 'Sucesión intestada']);
});

test('correo opcional: el botón "Sin correo" o un "no" cierran la solicitud', () => {
  for (const [text, buttonId] of [['Sin correo', 'cita_sin_correo'], ['No, gracias', '']]) {
    const env = citaEnv();
    const store = granted();
    for (const m of ['Quiero agendar una cita', 'Rosa Díaz', 'Un contrato de alquiler', 'virtual', 'lunes 10 am']) turn(m, store, { env });
    const r = turn(text, store, { buttonId, env });
    assert.strictEqual(r.saved, true, text);
    assert.strictEqual(decryptLine(lines(env.CITAS_FILE_PATH)[0]).correo, '');
  }
});

test('respuesta no válida: vuelve a preguntar el mismo dato (con los botones de modalidad)', () => {
  const store = granted();
  const env = citaEnv();
  for (const m of ['Quiero una cita', 'Rosa Díaz', 'Tenencia de mi hijo']) turn(m, store, { env });
  const r = turn('no sé', store, { env });
  assert.ok(r.reply.startsWith('Disculpe, no le entendí.'), r.reply);
  assert.strictEqual(r.waMessage.type, 'interactive');
  assert.strictEqual(turn('ok', store, { env: citaEnv() }).waMessage.type, 'interactive');
});

test('una duda en medio de la solicitud va a la IA, que sabe qué falta; luego se repite la pregunta', () => {
  const store = granted();
  const env = citaEnv();
  for (const m of ['Quiero una cita', 'Rosa Díaz', 'Tenencia de mi hijo']) turn(m, store, { env });
  const r = route({ text: '¿Cuánto cuesta la consulta?' }, store, env);
  assert.strictEqual(r.action, 'llm');
  const ctx = prepare(env, store, { text: '¿Cuánto cuesta la consulta?' });
  assert.ok(/Solicitud de cita: en curso .*falta: modalidad/.test(ctx.llmBody.messages[0].content));
  const out = runCode('Procesar respuesta', { input: { message: { content: 'El costo se lo indicará el equipo del estudio.' } }, store, refs: { 'Preparar solicitud LLM': ctx } })[0].json;
  const f = finish(out, store, env);
  assert.strictEqual(f.reply, 'El costo se lo indicará el equipo del estudio.\n\n¿Prefiere una cita presencial en nuestra oficina o virtual?');
  assert.strictEqual(store.conversations[INPUT.from].messages.at(-1).content, f.reply);
  // La siguiente respuesta se toma como la modalidad.
  assert.ok(turn('presencial', store, { env }).reply.includes('¿Qué día'));
});

test('si no se pudo guardar, el mensaje NO dice "registrada" y da los canales directos', () => {
  const store = granted();
  const env = { CITAS_FILE_KEY: 'corta', CITAS_FILE_PATH: citaEnv().CITAS_FILE_PATH, FIRM_NOTIFY_WHATSAPP: '51900000001' };
  for (const m of ['Quiero una cita', 'Rosa Díaz', 'Tenencia de mi hijo', 'virtual', 'viernes 4 pm']) turn(m, store, { env });
  const r = turn('no', store, { env });
  assert.strictEqual(r.saved, false);
  assert.ok(!/registrad/i.test(r.reply), r.reply);
  assert.ok(r.reply.includes('956 771 681'));
  assert.strictEqual(body(r), r.reply);
  assert.strictEqual(store.conversations[INPUT.from].messages.at(-1).content, r.reply);
  assert.strictEqual(store.citaFlow[INPUT.from].estado, 'fallida');
  assert.ok(r.notifyWhatsApp && r.notice.includes('no se pudo guardar'), 'el estudio recibe el aviso');
});

test('la IA dice "registrada" sin bloque <cita> y sin guardar nada: no se le dice eso al usuario', () => {
  const env = citaEnv();
  // Sin solicitud en curso.
  let store = granted();
  let { out } = parse({ message: { content: 'Su solicitud de cita quedó registrada; el equipo le confirmará.' } }, 'ollama', store);
  let f = finish(out, store, env);
  assert.strictEqual(f.saved, false);
  assert.ok(f.reply.startsWith('Aún no he registrado su solicitud de cita.'), f.reply);
  assert.ok(!/quedó registrada/.test(store.conversations[INPUT.from].messages.at(-1).content));
  // Con una solicitud en curso: se vuelve a pedir el dato pendiente.
  store = granted();
  for (const m of ['Quiero una cita', 'Rosa Díaz']) turn(m, store, { env });
  const ctx = prepare(env, store, { text: '¿Atienden sábados?' });
  out = runCode('Procesar respuesta', { input: { message: { content: 'Listo, su cita está agendada para el sábado.' } }, store, refs: { 'Preparar solicitud LLM': ctx } })[0].json;
  f = finish(out, store, env);
  assert.strictEqual(f.reply, 'Aún no he registrado su solicitud de cita. ¿Podría contarme en una frase el tema de su consulta?');
  assert.strictEqual(lines(env.CITAS_FILE_PATH).length, 0);
  // "hemos registrado su autorización" no es una cita: no se toca.
  store = granted();
  ({ out } = parse({ message: { content: 'Ya hemos registrado su autorización. ¿En qué puedo ayudarle?' } }, 'ollama', store));
  assert.strictEqual(finish(out, store, env).reply, 'Ya hemos registrado su autorización. ¿En qué puedo ayudarle?');
});

test('respaldo: el bloque <cita> de la IA se guarda (origen "ia") si el flujo no registró la cita', () => {
  const env = citaEnv();
  const store = granted();
  const { out } = parse({ message: { content: `Su solicitud de cita quedó registrada.\n<cita>${CITA}</cita>` } }, 'ollama', store);
  const f = finish(out, store, env);
  assert.strictEqual(f.saved, true);
  assert.strictEqual(f.origen, 'ia');
  assert.strictEqual(f.reply, 'Su solicitud de cita quedó registrada.');
  assert.strictEqual(decryptLine(lines(env.CITAS_FILE_PATH)[0]).origen, 'ia');
});

test('respaldo: con una solicitud en curso, los datos del bloque <cita> completan lo que falta', () => {
  const env = citaEnv();
  const store = granted();
  for (const m of ['Quiero una cita', 'María Pérez Ríos']) turn(m, store, { env });
  const ctx = prepare(env, store, { text: '¿Atienden los sábados?' });
  const incompleta = CITA.replace('"completa"', '"incompleta"');
  const out = runCode('Procesar respuesta', { input: { message: { content: `Tramitamos demandas de pensión.\n<cita>${incompleta}</cita>` } }, store, refs: { 'Preparar solicitud LLM': ctx } })[0].json;
  finish(out, store, env);
  const d = store.citaFlow[INPUT.from].datos;
  assert.deepStrictEqual([d.tema, d.modalidad, d.horario], ['x', 'presencial', 'martes']);
  assert.strictEqual(store.citaFlow[INPUT.from].estado, 'en_curso');
});

test('después de registrar en el flujo, un <cita> repetido de la IA no se vuelve a guardar', () => {
  const env = citaEnv();
  const store = granted();
  for (const m of ['Quiero una cita', 'Rosa Díaz', 'Tenencia de mi hijo', 'virtual', 'viernes 4 pm', 'no']) turn(m, store, { env });
  assert.strictEqual(lines(env.CITAS_FILE_PATH).length, 1);
  const { out } = parse({ message: { content: `Gracias, su solicitud quedó registrada.\n<cita>${CITA}</cita>` } }, 'ollama', store);
  const f = finish(out, store, env);
  assert.strictEqual(lines(env.CITAS_FILE_PATH).length, 1);
  assert.strictEqual(f.reply, 'Gracias, su solicitud quedó registrada.', 'ya está registrada: el mensaje es cierto');
});

test('si la IA pidió el nombre, n8n toma el relevo con la respuesta', () => {
  const store = granted();
  store.conversations = { [INPUT.from]: { updatedAt: Date.now(), messages: [
    { role: 'user', content: 'Tengo un problema con un terreno' }, { role: 'assistant', content: 'Con gusto le ayudo. ¿Me indica su nombre completo, por favor?' },
  ] } };
  const r = turn('Carlos Mendoza', store, { env: citaEnv() });
  assert.ok(r.reply.includes('tema de su consulta'), r.reply);
  assert.strictEqual(store.citaFlow[INPUT.from].datos.nombre, 'Carlos Mendoza');
  // Una pregunta general de la IA no inicia una solicitud.
  const other = granted();
  other.conversations = { [INPUT.from]: { updatedAt: Date.now(), messages: [
    { role: 'user', content: 'Hola' }, { role: 'assistant', content: '¿En qué puedo ayudarle con su caso?' },
  ] } };
  assert.strictEqual(route({ text: 'Quiero saber de herencias' }, other).action, 'llm');
  assert.ok(!other.citaFlow[INPUT.from]);
});

test('cancelar, rechazar el consentimiento o /reiniciar borran la solicitud en curso', () => {
  let store = granted();
  for (const m of ['Quiero una cita', 'Rosa Díaz']) turn(m, store, { env: citaEnv() });
  assert.ok(turn('cancelar', store).reply.startsWith('Entendido, dejé sin efecto'));
  assert.ok(!store.citaFlow[INPUT.from]);

  store = granted();
  for (const m of ['Quiero una cita', 'Rosa Díaz']) turn(m, store, { env: citaEnv() });
  const r = turn('No acepto', store, { buttonId: 'consent_no' });
  assert.ok(r.reply.includes('no usaremos sus datos personales'));
  assert.ok(!store.citaFlow[INPUT.from], 'sin consentimiento no se conservan los datos de la cita');

  store = granted();
  for (const m of ['Quiero una cita', 'Rosa Díaz']) turn(m, store, { env: citaEnv() });
  turn('/reiniciar', store, { env: { TEST_NUMBERS: INPUT.from } });
  assert.ok(!store.citaFlow[INPUT.from]);
});

test('sin consentimiento no hay solicitud en curso ni se guarda nada', () => {
  const store = {};
  const r = route({ text: 'Quiero una cita' }, store);
  assert.strictEqual(r.waMessage.interactive.body.text, CONSENT_TEXT);
  assert.ok(!store.citaFlow || !store.citaFlow[INPUT.from]);
  assert.strictEqual(finish(r, store, citaEnv()).saved, false);
});

// ---------- Indicador "escribiendo…" ----------

test('indicador de escritura: solo en el camino a la IA, antes de Preparar solicitud LLM', () => {
  assert.strictEqual(wf.connections['¿Llamar a la IA?'].main[0][0].node, 'Indicador escribiendo');
  assert.strictEqual(wf.connections['Indicador escribiendo'].main[0][0].node, 'Preparar solicitud LLM');
  assert.strictEqual(wf.connections['¿Llamar a la IA?'].main[1][0].node, 'Guardar cita cifrada');
});

test('indicador de escritura: formato de Meta (status "read" + typing_indicator) y nunca bloquea', () => {
  const n = nodes['Indicador escribiendo'];
  assert.strictEqual(n.onError, 'continueRegularOutput');
  assert.ok(!n.retryOnFail);
  assert.strictEqual(n.parameters.method, 'POST');
  const g = gate({ text: 'Hola', messageId: 'wamid.ABC', phoneNumberId: '200' });
  const body = JSON.parse(evalParam(n.parameters.jsonBody, { $json: g }));
  assert.deepStrictEqual(body, { messaging_product: 'whatsapp', status: 'read', message_id: 'wamid.ABC', typing_indicator: { type: 'text' } });
  // Responde por la línea (phone_number_id) del mensaje entrante.
  const url = evalParam(n.parameters.url, { $json: g, $env: { META_API_VERSION: 'v25.0', PHONE_NUMBER_ID: '999' } });
  assert.strictEqual(url, 'https://graph.facebook.com/v25.0/200/messages');
  assert.ok(evalParam(n.parameters.headerParameters.parameters[0].value, { $env: { META_ACCESS_TOKEN: 'x' } }) === 'Bearer x');
});

test('si el indicador falla, Preparar solicitud LLM usa igual los datos de la puerta', () => {
  const g = route({ text: 'Hola' });
  const ctx = runCode('Preparar solicitud LLM', { input: { error: { message: '(#131000) Something went wrong' } }, env: POLICY, store: {}, refs: { 'Estado de la cita': g } })[0].json;
  assert.strictEqual(ctx.from, INPUT.from);
  assert.ok(ctx.llmBody.messages.at(-1).content.endsWith('Hola'));
});

test('Enviar respuesta WhatsApp arma texto o interactivo y usa la línea entrante', () => {
  const n = nodes['Enviar respuesta WhatsApp'];
  const g = gate({ text: 'Quiero una cita', phoneNumberId: '300' });
  const body = JSON.parse(evalParam(n.parameters.jsonBody, { $json: g }));
  assert.strictEqual(body.to, INPUT.from);
  assert.strictEqual(body.type, 'interactive');
  assert.strictEqual(body.messaging_product, 'whatsapp');
  assert.strictEqual(evalParam(n.parameters.url, { $json: g, $env: { META_API_VERSION: 'v25.0' } }), 'https://graph.facebook.com/v25.0/300/messages');
});

// ---------- /reiniciar (solo números de prueba) ----------

test('/reiniciar desde un número de TEST_NUMBERS borra historial, consentimiento y control de citas', () => {
  const store = { ...granted(), conversations: { [INPUT.from]: { updatedAt: Date.now(), messages: [{ role: 'user', content: 'Hola' }] } }, citas: { [INPUT.from]: { hash: 'x', notified: true, updatedAt: Date.now() } } };
  store.consents['51911111111'] = { estado: 'otorgado', version: '2026-10' };
  const g = gate({ text: ' /Reiniciar ' }, store, { TEST_NUMBERS: '51 911 111 111, +51 900 000 000' });
  assert.strictEqual(g.action, 'reply');
  assert.strictEqual(g.reply, 'Conversación reiniciada.');
  assert.strictEqual(g.waMessage.text.body, 'Conversación reiniciada.');
  assert.ok(!store.conversations[INPUT.from] && !store.consents[INPUT.from] && !store.citas[INPUT.from]);
  assert.ok(store.consents['51911111111'], 'no toca a otros números');
  // Después del reinicio, el consentimiento vuelve a pedirse.
  assert.strictEqual(gate({ text: 'Quiero una cita' }, store).waMessage.type, 'interactive');
});

test('/reiniciar desde un número no permitido es un mensaje normal (va a la IA y no borra nada)', () => {
  for (const env of [{ TEST_NUMBERS: '51911111111' }, {}]) {
    const store = granted();
    const g = gate({ text: '/reiniciar' }, store, env);
    assert.strictEqual(g.action, 'llm');
    assert.strictEqual(store.consents[INPUT.from].estado, 'otorgado');
  }
});

// ---------- Extraer mensaje ----------

test('Extraer mensaje: lee el id del botón de consentimiento', () => {
  const body = { entry: [{ changes: [{ value: { metadata: { phone_number_id: '100' }, messages: [{ id: 'wamid.B', from: '519', type: 'interactive', interactive: { type: 'button_reply', button_reply: { id: 'consent_si', title: 'Acepto' } } }] } }] }] };
  const out = runCode('Extraer mensaje', { input: { body }, store: {} })[0].json;
  assert.strictEqual(out.buttonId, 'consent_si');
  assert.strictEqual(out.text, 'Acepto');
});

test('Extraer mensaje: ignora duplicados por message.id', () => {
  const store = {};
  const body = { entry: [{ changes: [{ value: { metadata: { phone_number_id: '100' }, messages: [{ id: 'wamid.X', from: '519', type: 'text', text: { body: 'Hola' } }] } }] }] };
  assert.strictEqual(runCode('Extraer mensaje', { input: { body }, store }).length, 1);
  assert.strictEqual(runCode('Extraer mensaje', { input: { body }, store }).length, 0);
});

// ---------- Ejecución ----------

let failed = 0;
for (const [name, fn] of tests) {
  try {
    fn();
    console.log(`ok   - ${name}`);
  } catch (e) {
    failed += 1;
    console.log(`FALLA - ${name}\n       ${e.message}`);
  }
}
console.log(`\n${tests.length - failed}/${tests.length} pruebas superadas`);
process.exit(failed ? 1 : 0);
