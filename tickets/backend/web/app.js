'use strict';

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const CATEGORIES = [
  { id: 'tarea', label: 'Tarea', color: '#9b86df', bg: '#f0eafb', fg: '#8262c3' },
  { id: 'examen', label: 'Examen', color: '#ebae69', bg: '#fff0de', fg: '#ae7737' },
  { id: 'trabajo', label: 'Trabajo', color: '#7ca8dc', bg: '#eaf1fc', fg: '#587eaf' },
  { id: 'proyecto personal', label: 'Proyecto personal', color: '#d998b4', bg: '#fbeaf3', fg: '#b56b90' },
  { id: 'quehaceres', label: 'Quehaceres', color: '#7bbba4', bg: '#e5f3ed', fg: '#528f74' },
  { id: 'vida social', label: 'Vida social', color: '#dfbe65', bg: '#faf3da', fg: '#a78b39' },
];
const STATUSES = ['pendiente', 'en progreso', 'resuelto'];
const LABELS = { pendiente: 'Pendiente', 'en progreso': 'En progreso', resuelto: 'Resuelto' };
const state = { user: null, tasks: [], view: 'board', layout: 'board', authMode: 'login', editing: null, taskId: null, loaded: false, refreshing: false, saving: false, pending: new Set() };
let toastTimer;
const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
const category = (id) => CATEGORIES.find((item) => item.id === id) || CATEGORIES[0];
const formatDate = (value, full = false) => new Intl.DateTimeFormat('es-PE', { day: 'numeric', month: 'short', ...(full ? { year: 'numeric', hour: '2-digit', minute: '2-digit' } : {}) }).format(new Date(value));
const localDate = (value) => { const d = new Date(value); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`; };
const localInput = (value) => { const date = new Date(value); return `${localDate(date)}T${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`; };
const activeTime = (task) => Number(task.tracked_ms) + (task.active_since ? Math.max(0, Date.now() - task.active_since) : 0);
const elapsedTime = (task) => Math.max(0, (task.resolved_at || Date.now()) - task.created_at);

function duration(ms) {
  if (!ms) return '0 min';
  const minutes = Math.floor(ms / 60000);
  if (minutes < 1) return '< 1 min';
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} h${minutes % 60 ? ` ${minutes % 60} min` : ''}`;
  return `${Math.floor(hours / 24)} d${hours % 24 ? ` ${hours % 24} h` : ''}`;
}

function setTheme(theme) {
  document.body.dataset.theme = theme;
  try { localStorage.setItem('ritmo-theme', theme); } catch { /* Theme persistence is optional. */ }
  $('#theme-label').textContent = theme === 'dark' ? 'Modo claro' : 'Modo oscuro';
  $$('[data-action="theme"]').forEach((button) => button.setAttribute('aria-label', theme === 'dark' ? 'Activar modo claro' : 'Activar modo oscuro'));
}

function toast(message) {
  clearTimeout(toastTimer);
  $('#toast').textContent = message;
  $('#toast').hidden = false;
  toastTimer = setTimeout(() => { $('#toast').hidden = true; }, 4500);
}

async function api(path, options = {}) {
  const method = options.method || 'GET';
  const attempts = method === 'GET' || method === 'PUT' ? 3 : 1;
  for (let attempt = 0; attempt < attempts; attempt++) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 20000);
    try {
      const response = await fetch(`/api${path}`, {
        method, credentials: 'same-origin', signal: controller.signal,
        ...(options.body ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(options.body) } : {}),
      });
      const data = await response.json();
      if (!response.ok) {
        const error = new Error(data.error || 'No pudimos completar la solicitud.');
        error.status = response.status;
        throw error;
      }
      return data;
    } catch (error) {
      const retryable = !error.status || error.status >= 500 || error.status === 429;
      if (retryable && attempt + 1 < attempts) {
        await new Promise((resolve) => setTimeout(resolve, 500 * (2 ** attempt)));
        continue;
      }
      if (!error.status) error.message = 'No pudimos conectar. Revisa tu conexión y vuelve a intentarlo.';
      throw error;
    } finally { clearTimeout(timer); }
  }
}

function showAuth() {
  state.user = null;
  state.tasks = [];
  state.loaded = false;
  $('#boot').hidden = true;
  $('#workspace').hidden = true;
  $('#auth').hidden = false;
  if ($('#task-dialog').open) $('#task-dialog').close();
}

function showWorkspace(user) {
  state.user = user;
  $('#boot').hidden = true;
  $('#auth').hidden = true;
  $('#workspace').hidden = false;
  $('#user-name').textContent = user.name;
  $('#avatar').textContent = user.name.trim().slice(0, 2).toUpperCase();
  $('#today').textContent = new Intl.DateTimeFormat('es-PE', { weekday: 'long', day: 'numeric', month: 'long' }).format(new Date());
  setView('board');
}

function authMode(mode) {
  state.authMode = mode;
  const register = mode === 'register';
  $('#name-field').hidden = !register;
  $('#auth-name').required = register;
  $('#auth-title').textContent = register ? 'Haz espacio para ti.' : 'Qué bueno verte.';
  $('#auth-subtitle').textContent = register ? 'Crea tu cuenta y encuentra tu propio ritmo.' : 'Ingresa y retoma donde lo dejaste.';
  $('#auth-submit').textContent = register ? 'Crear mi espacio' : 'Entrar a mi espacio';
  $('#auth-password').autocomplete = register ? 'new-password' : 'current-password';
  $('#auth-error').hidden = true;
  $$('[data-auth]').forEach((button) => { button.classList.toggle('selected', button.dataset.auth === mode); button.setAttribute('aria-pressed', button.dataset.auth === mode); });
}

function reportError(error) {
  if (error.status === 401) {
    showAuth();
    $('#auth-error').textContent = error.message;
    $('#auth-error').hidden = false;
  } else {
    $('#sync-error span').textContent = error.message;
    $('#sync-error').hidden = false;
    $('#save-indicator').textContent = 'No se pudo sincronizar. Reintenta.';
  }
}

async function refresh() {
  if (state.refreshing || !state.user) return;
  state.refreshing = true;
  const userId = state.user.id;
  $('#refresh').disabled = true;
  try {
    const data = await api('/tasks');
    if (state.user?.id !== userId) return;
    state.tasks = data.tasks;
    state.loaded = true;
    $('#sync-error').hidden = true;
    $('#save-indicator').textContent = 'Todos tus cambios están guardados';
    render();
  } catch (error) { if (state.user?.id === userId) reportError(error); }
  finally { state.refreshing = false; $('#refresh').disabled = false; }
}

function setView(view) {
  state.view = view;
  const copy = {
    board: ['Mis tareas', 'UN POCO DE ORDEN, MUCHO ESPACIO MENTAL', 'Vamos a tu ritmo', 'Tus pendientes, con un poquito más de claridad.'],
    history: ['Historial', 'CADA PASO CUENTA', 'Tu recorrido, hasta hoy', 'Todo lo que empezaste, avanzaste y lograste.'],
    analytics: ['Mi atención', 'TIEMPO PARA MIRAR EL PANORAMA', 'Lo que ocupa tu tiempo', 'Descubre tus patrones. Encuentra tu equilibrio.'],
  }[view];
  $('#breadcrumb-view').textContent = copy[0];
  $('#page-eyebrow').textContent = copy[1];
  $('#page-title').innerHTML = `${copy[2]}<span class="title-dot">.</span>`;
  $('#page-description').textContent = copy[3];
  $$('[data-view]').forEach((button) => { button.classList.toggle('active', button.dataset.view === view); button.setAttribute('aria-current', button.dataset.view === view ? 'page' : 'false'); });
  $('#task-section').hidden = view === 'analytics';
  $('#analytics-section').hidden = view === 'board';
  $('.view-switch').hidden = view !== 'board';
  $('#tasks-title').textContent = view === 'history' ? 'Historial de tareas' : 'Tu tablero';
  $('#tasks-subtitle').textContent = view === 'history' ? 'Incluye tareas pendientes, en progreso y resueltas.' : 'De pendiente a hecho. A tu manera.';
  render();
}

function filteredTasks() {
  const search = $('#search').value.trim().toLocaleLowerCase('es');
  return state.tasks.filter((task) => (!search || `${task.title} ${task.description}`.toLocaleLowerCase('es').includes(search))
    && (!$('#filter-category').value || task.category === $('#filter-category').value)
    && (!$('#filter-priority').value || task.priority === $('#filter-priority').value)
    && (!$('#filter-status').value || task.status === $('#filter-status').value))
    .sort((a, b) => state.view === 'history' ? b.created_at - a.created_at : new Date(a.deadline) - new Date(b.deadline));
}

function categoryChip(id) {
  const item = category(id);
  return `<span class="category-chip" style="--chip-bg:${item.bg};--chip-fg:${item.fg}">${item.label}</span>`;
}

function statusSelect(task, table = false) {
  return `<select class="${table ? 'table-status' : ''}" data-status-id="${task.id}" aria-label="Estado de ${escapeHtml(task.title)}" ${state.pending.has(task.id) ? 'disabled' : ''}>${STATUSES.map((status) => `<option value="${status}" ${status === task.status ? 'selected' : ''}>${LABELS[status]}</option>`).join('')}</select>`;
}

function card(task) {
  const overdue = task.status !== 'resuelto' && new Date(task.deadline).getTime() < Date.now();
  return `<article class="task-card"><div class="card-top">${categoryChip(task.category)}<button type="button" class="edit-button" data-edit="${task.id}" aria-label="Editar ${escapeHtml(task.title)}">···</button></div><button type="button" class="task-title-button" data-edit="${task.id}">${escapeHtml(task.title)}</button>${task.description ? `<p class="task-description">${escapeHtml(task.description)}</p>` : ''}<div class="task-details"><span class="deadline ${overdue ? 'overdue' : ''}" title="${escapeHtml(formatDate(task.deadline, true))}">${overdue ? 'Venció' : 'Límite'} · ${formatDate(task.deadline)}</span><span class="priority ${task.priority}">${{ alta: '↑ Alta', media: '— Media', baja: '↓ Baja' }[task.priority]}</span></div><div class="card-bottom">${statusSelect(task)}<span class="time-chip" title="Tiempo acumulado en progreso">◷ ${duration(activeTime(task))}</span></div></article>`;
}

function emptyState(filtered = false) {
  return `<div class="empty-state"><span class="empty-symbol" aria-hidden="true">${filtered ? '⌕' : '✦'}</span><h3>${filtered ? 'No encontramos esas tareas' : 'Tu próximo paso empieza aquí'}</h3><p>${filtered ? 'Prueba otra búsqueda o limpia los filtros para ver todo tu espacio.' : 'Un pendiente menos en tu cabeza. Crea tu primera tarea y dale su propio espacio.'}</p><button type="button" class="button ${filtered ? 'secondary' : 'primary'}" data-action="${filtered ? 'clear-filters' : 'create'}">${filtered ? 'Limpiar filtros' : '＋ Crear mi primera tarea'}</button></div>`;
}

function taskTable(tasks) {
  return `<div class="table-wrap"><table><thead><tr><th>Tarea</th><th>Estado</th><th>Prioridad</th><th>Origen</th><th>Fecha límite</th><th>Resolución</th><th>Hasta resolver</th><th>En progreso</th></tr></thead><tbody>${tasks.map((task) => `<tr><td><button type="button" class="task-title-button" data-edit="${task.id}">${escapeHtml(task.title)}</button>${categoryChip(task.category)}</td><td>${statusSelect(task, true)}</td><td><span class="priority ${task.priority}">${task.priority}</span></td><td>${formatDate(task.created_at, true)}</td><td>${formatDate(task.deadline, true)}</td><td>${task.resolved_at ? formatDate(task.resolved_at, true) : 'Por resolver'}</td><td>${duration(elapsedTime(task))}<small>${task.resolved_at ? 'desde su creación' : 'transcurrido · abierta'}</small></td><td>${duration(activeTime(task))}</td></tr>`).join('')}</tbody></table></div>`;
}

function render() {
  if (!state.user) return;
  const count = (status) => state.tasks.filter((task) => task.status === status).length;
  $('#stat-pending').textContent = count('pendiente');
  $('#stat-progress').textContent = count('en progreso');
  $('#stat-done').textContent = count('resuelto');
  $('#stat-today').textContent = state.tasks.filter((task) => task.status !== 'resuelto' && localDate(task.deadline) === localDate(Date.now())).length;
  $('#nav-count').textContent = state.tasks.filter((task) => task.status !== 'resuelto').length;
  if (state.view !== 'analytics') {
    const tasks = filteredTasks();
    if (!state.loaded) $('#task-results').innerHTML = '<div class="empty-state"><p>Cargando tus tareas…</p></div>';
    else if (!tasks.length) $('#task-results').innerHTML = emptyState(state.tasks.length > 0);
    else if (state.view === 'history' || state.layout === 'list') $('#task-results').innerHTML = taskTable(tasks);
    else $('#task-results').innerHTML = `<div class="board">${STATUSES.filter((status) => !$('#filter-status').value || status === $('#filter-status').value).map((status) => {
      const group = tasks.filter((task) => task.status === status);
      return `<section class="column" data-status="${status}"><div class="column-heading"><i class="status-dot" aria-hidden="true"></i><h3>${{ pendiente: 'Pendientes', 'en progreso': 'En progreso', resuelto: 'Resueltas' }[status]}</h3><span class="count-pill">${group.length}</span><button type="button" data-create-status="${status}" aria-label="Nueva tarea: ${LABELS[status]}">＋</button></div>${group.length ? group.map(card).join('') : `<div class="column-empty"><span aria-hidden="true">${status === 'resuelto' ? '✓' : '◌'}</span><p>${{ pendiente: 'Un espacio para lo que viene.', 'en progreso': 'Empieza cuando estés listo.', resuelto: 'Tus próximos logros van aquí.' }[status]}</p></div>`}<button type="button" class="column-add" data-create-status="${status}">＋ Agregar tarea</button></section>`;
    }).join('')}</div>`;
  }
  if (state.view !== 'board') renderAnalytics();
}

function renderAnalytics() {
  const groups = CATEGORIES.map((item) => {
    const tasks = state.tasks.filter((task) => task.category === item.id);
    return { ...item, count: tasks.length, time: tasks.reduce((sum, task) => sum + activeTime(task), 0) };
  });
  const totalTime = groups.reduce((sum, item) => sum + item.time, 0);
  const maxTime = Math.max(1, ...groups.map((item) => item.time));
  $('#time-chart').innerHTML = groups.map((item) => `<div class="bar-row"><div class="bar-label"><span>${item.label}</span><span>${duration(item.time)} · ${totalTime ? Math.round(item.time / totalTime * 100) : 0}%</span></div><div class="bar-track" role="img" aria-label="${item.label}: ${duration(item.time)}"><div class="bar-fill" style="width:${item.time / maxTime * 100}%;background:${item.color}"></div></div></div>`).join('');
  const top = [...groups].sort((a, b) => b.time - a.time)[0];
  $('#attention-insight').textContent = totalTime ? `${top.label} recibe la mayor parte de tu atención: ${Math.round(top.time / totalTime * 100)}% de tu tiempo en progreso.` : 'Pon una tarea en progreso para empezar a conocer tus hábitos.';
  let cumulative = 0;
  const stops = groups.filter((item) => item.count > 0).map((item) => { const start = cumulative; cumulative += item.count / state.tasks.length * 100; return `${item.color} ${start}% ${cumulative}%`; });
  $('#donut').style.background = stops.length ? `conic-gradient(${stops.join(',')})` : 'var(--surface-soft)';
  $('#donut').setAttribute('role', 'img');
  $('#donut').setAttribute('aria-label', `Distribución de ${state.tasks.length} tareas por categoría; valores en la leyenda.`);
  $('#donut-total').textContent = state.tasks.length;
  $('#category-legend').innerHTML = groups.map((item) => `<div class="legend-row"><i style="background:${item.color}" aria-hidden="true"></i><span>${item.label}</span><strong>${item.count}</strong></div>`).join('');
  const resolved = state.tasks.filter((task) => task.status === 'resuelto');
  $('#completion-rate').textContent = `${state.tasks.length ? Math.round(resolved.length / state.tasks.length * 100) : 0}%`;
  $('#average-time').textContent = resolved.length ? duration(resolved.reduce((sum, task) => sum + elapsedTime(task), 0) / resolved.length) : 'Sin datos';
  $('#total-focus').textContent = duration(totalTime);
}

function openTask(id = null, status = 'pendiente') {
  if (state.saving) return;
  const task = state.tasks.find((item) => item.id === id);
  if (task && state.pending.has(task.id)) return;
  state.editing = task || null;
  state.taskId = task?.id || crypto.randomUUID();
  $('#task-form').reset();
  $('#task-error').hidden = true;
  $('#dialog-title').textContent = task ? 'Cada detalle cuenta' : 'Una nueva tarea';
  $('#task-title').value = task?.title || '';
  $('#task-description').value = task?.description || '';
  $('#task-category').value = task?.category || $('#filter-category').value || 'tarea';
  $('#task-priority').value = task?.priority || 'media';
  $('#task-status').value = task?.status || status;
  const tomorrow = new Date(); tomorrow.setDate(tomorrow.getDate() + 1); tomorrow.setHours(18, 0, 0, 0);
  $('#task-deadline').value = localInput(task?.deadline || tomorrow);
  $('#created-note').textContent = task ? `Creada el ${formatDate(task.created_at, true)} · ${duration(activeTime(task))} en progreso` : 'La fecha de origen se registra al guardar.';
  $('#task-dialog').showModal();
  $('#task-title').focus();
}

function updateTask(task, ownerId) {
  if (state.user?.id !== ownerId) return;
  const index = state.tasks.findIndex((item) => item.id === task.id);
  if (index < 0) state.tasks.unshift(task); else state.tasks[index] = task;
  state.loaded = true;
  $('#sync-error').hidden = true;
  $('#save-indicator').textContent = 'Todos tus cambios están guardados';
  render();
}

async function changeStatus(id, status) {
  const task = state.tasks.find((item) => item.id === id);
  if (!task || state.pending.has(id) || task.status === status) return;
  const ownerId = state.user.id;
  state.pending.add(id);
  render();
  $('#save-indicator').textContent = 'Guardando cambio…';
  try {
    const data = await api(`/tasks/${id}`, { method: 'PATCH', body: { ...task, status } });
    updateTask(data.task, ownerId);
    if (state.user?.id === ownerId) toast(status === 'resuelto' ? 'Una cosa menos. ¡Bien hecho!' : 'Estado actualizado');
  } catch (error) {
    if (state.user?.id === ownerId) {
      reportError(error);
      if (error.status === 409) await refresh();
    }
  } finally { state.pending.delete(id); render(); }
}

$('#auth-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const button = $('#auth-submit');
  if (button.disabled) return;
  button.disabled = true;
  $$('[data-auth]').forEach((tab) => { tab.disabled = true; });
  $('#auth-error').hidden = true;
  button.textContent = 'Preparando tu espacio…';
  try {
    const data = await api(`/${state.authMode === 'register' ? 'register' : 'login'}`, { method: 'POST', body: { name: $('#auth-name').value.trim(), email: $('#auth-email').value.trim(), password: $('#auth-password').value } });
    $('#auth-password').value = '';
    showWorkspace(data.user);
    await refresh();
  } catch (error) { $('#auth-error').textContent = error.message; $('#auth-error').hidden = false; }
  finally { button.disabled = false; $$('[data-auth]').forEach((tab) => { tab.disabled = false; }); button.textContent = state.authMode === 'register' ? 'Crear mi espacio' : 'Entrar a mi espacio'; }
});

$('#task-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  if (state.saving) return;
  const ownerId = state.user?.id;
  const deadline = new Date($('#task-deadline').value);
  if (Number.isNaN(deadline.getTime())) { $('#task-error').textContent = 'Selecciona una fecha límite válida.'; $('#task-error').hidden = false; return; }
  state.saving = true;
  $('#save-task').disabled = true;
  $('#save-task').textContent = 'Guardando…';
  $('#task-error').hidden = true;
  try {
    const data = await api(`/tasks/${state.taskId}`, { method: state.editing ? 'PATCH' : 'PUT', body: {
      title: $('#task-title').value.trim(), description: $('#task-description').value.trim(),
      category: $('#task-category').value, priority: $('#task-priority').value, status: $('#task-status').value,
      deadline: deadline.toISOString(), ...(state.editing ? { version: state.editing.version } : {}),
    } });
    updateTask(data.task, ownerId);
    $('#task-dialog').close();
    toast(state.editing ? 'Tarea actualizada' : 'Tu tarea ya tiene su espacio');
  } catch (error) {
    if (error.status === 401) reportError(error);
    else { $('#task-error').textContent = error.message; $('#task-error').hidden = false; }
    if (error.status === 409) {
      await refresh();
      const current = state.tasks.find((item) => item.id === state.taskId);
      if (current) state.editing = current;
      $('#task-error').textContent = 'Había un cambio en otra ventana. Revisa tus datos y vuelve a guardar para aplicar esta edición.';
    }
  } finally { state.saving = false; $('#save-task').disabled = false; $('#save-task').textContent = 'Guardar tarea'; }
});

document.addEventListener('click', (event) => {
  const button = event.target.closest('button');
  if (!button) return;
  if (button.dataset.auth) authMode(button.dataset.auth);
  if (button.dataset.view) setView(button.dataset.view);
  if (button.dataset.layout) { state.layout = button.dataset.layout; $$('[data-layout]').forEach((item) => { item.classList.toggle('selected', item.dataset.layout === state.layout); item.setAttribute('aria-pressed', item.dataset.layout === state.layout); }); render(); }
  if (button.dataset.edit) openTask(button.dataset.edit);
  if (button.dataset.createStatus) openTask(null, button.dataset.createStatus);
  if (button.dataset.action === 'theme') setTheme(document.body.dataset.theme === 'dark' ? 'light' : 'dark');
  if (button.dataset.action === 'create') openTask();
  if (button.dataset.action === 'reload') refresh();
  if (button.dataset.action === 'clear-filters') { ['#search', '#filter-category', '#filter-priority', '#filter-status'].forEach((selector) => { $(selector).value = ''; }); render(); }
});
document.addEventListener('change', (event) => { if (event.target.dataset.statusId) changeStatus(event.target.dataset.statusId, event.target.value); });
$('#new-task').addEventListener('click', () => openTask());
$('#refresh').addEventListener('click', refresh);
$('#close-dialog').addEventListener('click', () => { if (!state.saving) $('#task-dialog').close(); });
$('#cancel-task').addEventListener('click', () => { if (!state.saving) $('#task-dialog').close(); });
$('#task-dialog').addEventListener('cancel', (event) => { if (state.saving) event.preventDefault(); });
$('#search').addEventListener('input', render);
['#filter-category', '#filter-priority', '#filter-status'].forEach((selector) => $(selector).addEventListener('change', render));
$('#logout').addEventListener('click', async () => {
  $('#logout').disabled = true;
  try { await api('/logout', { method: 'POST', body: {} }); showAuth(); toast('Hasta pronto. Tus tareas te esperan.'); }
  catch (error) { reportError(error); }
  finally { $('#logout').disabled = false; }
});
document.addEventListener('visibilitychange', () => { if (!document.hidden && state.user && !state.saving && !state.pending.size && !$('#task-dialog').open) refresh(); });
setInterval(() => { if (state.user && !document.hidden && !state.pending.size && !state.saving && !$('#task-dialog').open && !$('#task-results').contains(document.activeElement)) render(); }, 60000);

async function init() {
  let theme;
  try { theme = localStorage.getItem('ritmo-theme'); } catch { /* Browser preference still works. */ }
  setTheme(theme === 'dark' || theme === 'light' ? theme : matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
  const options = CATEGORIES.map((item) => `<option value="${item.id}">${item.label}</option>`).join('');
  $('#task-category').innerHTML = options;
  $('#filter-category').insertAdjacentHTML('beforeend', options);
  authMode('login');
  try { const data = await api('/me'); showWorkspace(data.user); await refresh(); }
  catch (error) { showAuth(); if (error.status !== 401) { $('#auth-error').textContent = error.message; $('#auth-error').hidden = false; } }
}
init();
