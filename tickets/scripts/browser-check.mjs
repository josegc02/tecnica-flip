// Optional browser QA with an installed Chrome/Edge. No npm dependencies.
import { spawn } from 'node:child_process';
import { readFile, mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const output = path.join(root, '.local', 'browser-qa');
const profile = path.join(output, `profile-${randomUUID()}`);
const executable = process.env.BROWSER_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const base = process.argv[2] || 'http://127.0.0.1:8000';
await mkdir(output, { recursive: true });
const browser = spawn(executable, ['--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check', '--remote-debugging-port=0', `--user-data-dir=${profile}`, 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'], windowsHide: true });
let browserLog = '';
browser.stderr.on('data', (chunk) => { browserLog = (browserLog + chunk.toString()).slice(-5000); });
let socket;
let sequence = 0;
const pending = new Map();
const errors = [];
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function until(callback, description, timeout = 15000) {
  const start = Date.now();
  while (Date.now() - start < timeout) {
    if (await callback()) return;
    await pause(100);
  }
  throw new Error(`Timeout: ${description}`);
}

function call(method, params = {}) {
  return new Promise((resolve, reject) => {
    const id = ++sequence;
    const timer = setTimeout(() => { pending.delete(id); reject(new Error(`CDP timeout: ${method}`)); }, 15000);
    pending.set(id, { resolve: (result) => { clearTimeout(timer); resolve(result); }, reject: (error) => { clearTimeout(timer); reject(error); } });
    socket.send(JSON.stringify({ id, method, params }));
  });
}

async function evaluate(expression) {
  const result = await call('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
  if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
  return result.result.value;
}
const waitFor = (expression, label) => until(() => evaluate(expression), label);
const click = (selector) => evaluate(`document.querySelector(${JSON.stringify(selector)}).click()`);
const fill = (values) => evaluate(`Object.entries(${JSON.stringify(values)}).forEach(([id, value]) => { document.querySelector(id).value = value; });`);
async function screenshot(name) {
  const { data } = await call('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true });
  await writeFile(path.join(output, name + '.png'), Buffer.from(data, 'base64'));
}

try {
  let port;
  await until(async () => {
    try { port = (await readFile(path.join(profile, 'DevToolsActivePort'), 'utf8')).split('\n')[0]; return !!port; }
    catch { return false; }
  }, 'inicio de Chrome', 20000);
  const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  const target = targets.find((item) => item.type === 'page');
  socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { socket.addEventListener('open', resolve, { once: true }); socket.addEventListener('error', reject, { once: true }); });
  socket.addEventListener('message', ({ data }) => {
    const message = JSON.parse(data);
    if (message.id && pending.has(message.id)) {
      const waiter = pending.get(message.id); pending.delete(message.id);
      if (message.error) waiter.reject(new Error(JSON.stringify(message.error))); else waiter.resolve(message.result);
    }
    if (message.method === 'Runtime.exceptionThrown') errors.push(message.params.exceptionDetails);
  });
  await call('Runtime.enable');
  await call('Page.enable');
  await call('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });
  await call('Page.navigate', { url: base });
  await waitFor('document.querySelector("#auth") && !document.querySelector("#auth").hidden', 'pantalla de ingreso');
  await screenshot('01-ingreso');
  const email = `browser-${randomUUID()}@example.invalid`;
  const password = `Test-${randomUUID()}`;
  await click('[data-auth="register"]');
  await fill({ '#auth-name': 'Ana de prueba', '#auth-email': email, '#auth-password': password });
  await evaluate('document.querySelector("#auth-form").requestSubmit()');
  await waitFor('state.user && state.loaded', 'registro y carga de tareas');
  assert.equal(await evaluate('state.tasks.length'), 0);
  await screenshot('02-inicio-vacio');

  const tasks = [
    ['Preparar la exposición de diseño', 'tarea', 'alta', 'pendiente'],
    ['Repasar para el examen de cálculo', 'examen', 'alta', 'en progreso'],
    ['Enviar la propuesta al equipo', 'trabajo', 'media', 'pendiente'],
    ['Avanzar mi portafolio personal', 'proyecto personal', 'media', 'en progreso'],
    ['Ordenar mi espacio de trabajo', 'quehaceres', 'baja', 'resuelto'],
    ['Organizar una cena con amigos', 'vida social', 'baja', 'resuelto'],
  ];
  for (const [index, [title, cat, priority, status]] of tasks.entries()) {
    await click('#new-task');
    await fill({ '#task-title': title, '#task-description': 'Un pequeño paso para organizar mejor la semana.', '#task-category': cat, '#task-priority': priority, '#task-status': status });
    await evaluate('document.querySelector("#task-form").requestSubmit()');
    await waitFor(`state.tasks.length === ${index + 1} && !state.saving`, `crear tarea ${index + 1}`);
  }
  await screenshot('03-tablero-claro');
  assert.equal(await evaluate('document.documentElement.scrollWidth <= innerWidth'), true, 'sin desborde horizontal en escritorio');
  await fill({ '#search': 'no existe esta tarea' });
  await evaluate('document.querySelector("#search").dispatchEvent(new Event("input", {bubbles:true}))');
  assert.equal(await evaluate('document.querySelector("#task-results").textContent.includes("No encontramos")'), true);
  await click('[data-action="clear-filters"]');

  const firstId = await evaluate('state.tasks.find(t => t.category === "tarea").id');
  await click(`[data-edit="${firstId}"]`);
  await fill({ '#task-title': '<img src=x onerror=alert(1)> Texto seguro', '#task-description': 'Texto editado desde el navegador.' });
  await evaluate('document.querySelector("#task-form").requestSubmit()');
  await waitFor('!state.saving && !document.querySelector("#task-dialog").open', 'editar tarea');
  assert.equal(await evaluate('document.querySelectorAll("#task-results img").length'), 0, 'contenido escapado');
  await evaluate(`const statusInput = document.querySelector('[data-status-id="${firstId}"]'); statusInput.value = 'en progreso'; statusInput.dispatchEvent(new Event('change', { bubbles: true }));`);
  await waitFor(`!state.pending.size && state.tasks.find(t => t.id === '${firstId}').status === 'en progreso'`, 'cambio de estado');
  await click('[data-view="history"]');
  assert.equal(await evaluate('document.querySelectorAll("tbody tr").length'), 6);
  assert.equal(await evaluate('!document.querySelector("#analytics-section").hidden'), true);
  await screenshot('04-historial');
  await click('[data-view="analytics"]');
  assert.equal(await evaluate('document.querySelector("#donut-total").textContent'), '6');
  await screenshot('05-analitica');
  await click('[data-action="theme"]');
  await click('[data-view="board"]');
  await screenshot('06-tablero-oscuro');
  await call('Page.reload', { ignoreCache: true });
  await waitFor('typeof state !== "undefined" && state.loaded', 'persistencia tras recarga');
  assert.equal(await evaluate('state.tasks.length'), 6);
  assert.equal(await evaluate('document.body.dataset.theme'), 'dark');
  await call('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
  await screenshot('07-movil-oscuro');
  assert.equal(await evaluate('document.documentElement.scrollWidth <= innerWidth'), true, 'sin desborde en móvil');
  await click('#new-task');
  await screenshot('08-formulario-movil');
  await click('#cancel-task');
  await click('#logout');
  await waitFor('!document.querySelector("#auth").hidden', 'cerrar sesión');
  await fill({ '#auth-email': email, '#auth-password': password });
  await evaluate('document.querySelector("#auth-form").requestSubmit()');
  await waitFor('state.loaded && state.user', 'reingresar');
  assert.equal(await evaluate('state.tasks.length'), 6);
  assert.equal(errors.length, 0, JSON.stringify(errors));
  console.log('OK: registro, seis categorías, creación, edición, estados, filtros, historial, gráficos, tema, recarga, sesión y vista móvil. Sin excepciones JavaScript.');
  console.log(`Capturas: ${output}`);
} catch (error) {
  console.error(browserLog);
  throw error;
} finally {
  if (socket?.readyState === WebSocket.OPEN) {
    try { await call('Browser.close'); } catch { /* Browser may close before acknowledging. */ }
    socket.close();
  }
  if (browser.exitCode === null) browser.kill();
}
