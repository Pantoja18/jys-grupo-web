// Ejecuta el código de los nodos "Code" de n8n_workflow_template.json fuera de n8n, con objetos
// simulados ($input, $('Nodo'), $env, $getWorkflowStaticData). Lo usan test_flujo.js y las pruebas
// de Python (test_flujo_n8n.py), estas últimas por línea de comandos:
//
//   echo {"node":"Puerta de consentimiento","input":{...},"env":{},"store":{},"refs":{}} | node pruebas/nodos_n8n.js
//   → {"output":[...],"store":{...}}

'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');

const ROOT = path.join(__dirname, '..');
const WORKFLOW_PATH = path.join(ROOT, 'n8n_workflow_template.json');
const PROMPT_PATH = path.join(ROOT, 'system_prompt.md');
const wf = JSON.parse(fs.readFileSync(WORKFLOW_PATH, 'utf8'));
const nodes = Object.fromEntries(wf.nodes.map((n) => [n.name, n]));

// Ejecuta el código de un nodo Code como lo haría n8n (modo "una vez para todos los ítems").
function runCode(nodeName, { input, env = {}, store = {}, refs = {} }) {
  assert.ok(nodes[nodeName], `no existe el nodo "${nodeName}"`);
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

module.exports = { wf, nodes, runCode, ROOT, PROMPT_PATH };

if (require.main === module) {
  const req = JSON.parse(fs.readFileSync(0, 'utf8'));
  const store = req.store || {};
  try {
    const output = runCode(req.node, { input: req.input, env: req.env, store, refs: req.refs });
    process.stdout.write(JSON.stringify({ output, store }));
  } catch (e) {
    process.stdout.write(JSON.stringify({ error: e.message }));
    process.exitCode = 1;
  }
}
