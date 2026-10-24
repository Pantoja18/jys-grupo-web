// Pruebas del flujo de n8n (n8n_workflow_template.json) sin n8n: ejecuta el código de los nodos
// "Code" con objetos simulados ($input, $env, $getWorkflowStaticData) y revisa la estructura del flujo.
//
// Uso, desde whatsapp-bot/:
//   node pruebas/test_flujo.js
// Sin Node instalado (PowerShell, con Docker):
//   docker run --rm -v "${PWD}:/w" -w /w node:20-alpine node pruebas/test_flujo.js

'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');

const ROOT = path.join(__dirname, '..');
const wf = JSON.parse(fs.readFileSync(path.join(ROOT, 'n8n_workflow_template.json'), 'utf8'));
const nodes = Object.fromEntries(wf.nodes.map((n) => [n.name, n]));
const PROMPT_PATH = path.join(ROOT, 'system_prompt.md');

// Ejecuta el código de un nodo Code como lo haría n8n (modo "una vez para todos los ítems").
function runCode(nodeName, { input, env = {}, store = {}, refs = {} }) {
  const code = nodes[nodeName].parameters.jsCode;
  const fn = new Function('$input', '$', '$env', '$getWorkflowStaticData', 'require', code);
  return fn(
    { first: () => ({ json: input }) },
    (name) => {
      assert.ok(name in refs, `el nodo hace referencia a "${name}", que la prueba no simuló`);
      return { first: () => ({ json: refs[name] }) };
    },
    { SYSTEM_PROMPT_PATH: PROMPT_PATH, ...env },
    () => store,
    require,
  );
}

const INPUT = { from: '51900000000', messageId: 'wamid.T1', contactName: 'Prueba', phoneNumberId: '100', text: 'Hola', needsAI: true, directReply: '' };

function prepare(env = {}, store = {}) {
  return runCode('Preparar solicitud LLM', { input: INPUT, env, store })[0].json;
}

function parse(response, provider = 'ollama', store = {}) {
  const ctx = prepare({ LLM_PROVIDER: provider }, store);
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

// ---------- Extraer mensaje (sin cambios en la Fase 0) ----------

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
