// Ejecuta el código de los nodos "Code" de n8n_workflow_template.json fuera de n8n, con objetos
// simulados ($input, $('Nodo'), $env, $getWorkflowStaticData). Lo usan test_flujo.js y las pruebas
// de Python (test_flujo_n8n.py), estas últimas por línea de comandos:
//
//   echo {"node":"Puerta de consentimiento","input":{...},"env":{},"store":{},"refs":{}} | node pruebas/nodos_n8n.js
//   → {"output":[...],"store":{...}}
//   echo {"param":["Indicador escribiendo","jsonBody"],"json":{...},"env":{...}} | node pruebas/nodos_n8n.js
//   → {"value": <resultado de la expresión>}

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

// Evalúa un parámetro con expresiones de n8n ("={{ ... }}" o texto con varias {{ ... }}).
function evalParam(value, { $json = {}, $env = {} } = {}) {
  assert.ok(typeof value === 'string' && value.startsWith('='), `no es una expresión: ${value}`);
  const run = (expr) => new Function('$json', '$env', `return (${expr});`)($json, $env);
  const tpl = value.slice(1);
  const whole = tpl.match(/^\{\{([\s\S]*)\}\}$/);
  if (whole && !whole[1].includes('}}')) return run(whole[1]);
  return tpl.replace(/\{\{([\s\S]*?)\}\}/g, (_, expr) => String(run(expr)));
}

module.exports = { wf, nodes, runCode, evalParam, ROOT, PROMPT_PATH };

if (require.main === module) {
  const req = JSON.parse(fs.readFileSync(0, 'utf8'));
  const store = req.store || {};
  try {
    if (req.param) {
      const [name, key] = req.param;
      process.stdout.write(JSON.stringify({ value: evalParam(nodes[name].parameters[key], { $json: req.json, $env: req.env || {} }) }));
      return;
    }
    const output = runCode(req.node, { input: req.input, env: req.env, store, refs: req.refs });
    process.stdout.write(JSON.stringify({ output, store }));
  } catch (e) {
    process.stdout.write(JSON.stringify({ error: e.message }));
    process.exitCode = 1;
  }
}
