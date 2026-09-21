/* Print Scheduler front end. Vanilla JS, no build step, no network beyond this app. */

const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

const state = { kind: 'once', source: 'upload', weekdays: new Set([0, 1, 2, 3, 4]), file: null };

/* ---------- helpers ---------- */

async function api(path, options = {}) {
  const opts = { ...options, headers: { 'X-Printsched': '1', ...(options.headers || {}) } };
  const res = await fetch(path, opts);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.error || `${res.status} ${res.statusText}`);
  return body;
}

function toast(message, kind = '') {
  const el = document.createElement('div');
  el.className = `toast ${kind}`;
  el.textContent = message;
  $('#toasts').append(el);
  setTimeout(() => el.remove(), 5200);
}

function whenText(iso) {
  if (!iso) return 'never';
  const then = new Date(iso.replace(' ', 'T'));
  if (Number.isNaN(then.getTime())) return iso;
  const now = new Date();
  const mins = Math.round((then - now) / 60000);
  const clock = then.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

  if (mins < 0) return `overdue (${clock})`;
  if (mins < 1) return 'in under a minute';
  if (mins < 60) return `in ${mins} min (${clock})`;

  const sameDay = then.toDateString() === now.toDateString();
  if (sameDay) return `today at ${clock}`;
  const tomorrow = new Date(now); tomorrow.setDate(now.getDate() + 1);
  if (then.toDateString() === tomorrow.toDateString()) return `tomorrow at ${clock}`;
  return `${then.toLocaleDateString([], { weekday: 'short', day: 'numeric', month: 'short' })} at ${clock}`;
}

function shortStamp(iso) {
  if (!iso) return '';
  const d = new Date(iso.replace(' ', 'T'));
  if (Number.isNaN(d.getTime())) return iso;
  return `${d.toLocaleDateString([], { day: '2-digit', month: 'short' })} ${d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`;
}

/* ---------- form wiring ---------- */

function buildDayPicker() {
  const wrap = $('#daypicker');
  wrap.innerHTML = '';
  DAYS.forEach((label, index) => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'day' + (state.weekdays.has(index) ? ' on' : '');
    btn.textContent = label;
    btn.setAttribute('aria-pressed', state.weekdays.has(index));
    btn.addEventListener('click', () => {
      state.weekdays.has(index) ? state.weekdays.delete(index) : state.weekdays.add(index);
      buildDayPicker();
    });
    wrap.append(btn);
  });
}

function selectKind(kind) {
  state.kind = kind;
  $$('#kind-picker .seg').forEach((b) => b.classList.toggle('active', b.dataset.kind === kind));
  $$('.when-pane').forEach((p) => p.classList.toggle('hidden', p.dataset.kind !== kind));
}

function selectSource(source) {
  state.source = source;
  $$('.tab').forEach((b) => b.classList.toggle('active', b.dataset.source === source));
  $$('.source-pane').forEach((p) => p.classList.toggle('hidden', p.dataset.pane !== source));
}

function showChosenFile(file) {
  state.file = file;
  const zone = $('#dropzone');
  zone.classList.toggle('filled', Boolean(file));
  if (file) {
    const kb = file.size < 1024 * 1024
      ? `${Math.max(1, Math.round(file.size / 1024))} KB`
      : `${(file.size / 1048576).toFixed(1)} MB`;
    zone.querySelector('.dz-text').textContent = file.name;
    zone.querySelector('.dz-hint').textContent = kb;
  } else {
    zone.querySelector('.dz-text').innerHTML = 'Drop a file here or <u>browse</u>';
    zone.querySelector('.dz-hint').textContent = 'PDF, images, plain text — anything your printer accepts';
  }
}

function defaultRunAt() {
  const soon = new Date(Date.now() + 10 * 60000);
  soon.setSeconds(0, 0);
  const pad = (n) => String(n).padStart(2, '0');
  return `${soon.getFullYear()}-${pad(soon.getMonth() + 1)}-${pad(soon.getDate())}T${pad(soon.getHours())}:${pad(soon.getMinutes())}`;
}

function collectForm() {
  const data = new FormData();
  data.append('kind', state.kind);
  data.append('name', $('#name').value.trim());
  data.append('copies', $('#copies').value || '1');
  data.append('printer', $('#printer').value);

  const extras = [];
  if ($('#sides').value) extras.push(`sides=${$('#sides').value}`);
  if ($('#options').value.trim()) extras.push($('#options').value.trim());
  data.append('options', extras.join(' '));

  if (state.kind === 'once') data.append('run_at', $('#run-at').value);
  if (state.kind === 'daily') data.append('time_of_day', $('#time-daily').value);
  if (state.kind === 'weekly') {
    data.append('time_of_day', $('#time-weekly').value);
    data.append('weekdays', Array.from(state.weekdays).sort((a, b) => a - b).join(','));
  }
  if (state.kind === 'interval') {
    const amount = parseInt($('#interval-amount').value, 10) || 0;
    data.append('interval_minutes', String(amount * parseInt($('#interval-unit').value, 10)));
  }

  if (state.source === 'upload') {
    if (!state.file) throw new Error('Choose a file to print.');
    data.append('file', state.file, state.file.name);
  } else {
    const path = $('#source-path').value.trim();
    if (!path) throw new Error('Type the full path of the file to print.');
    data.append('source_path', path);
    data.append('source_mode', $('#live-mode').checked ? 'live' : 'snapshot');
  }
  return data;
}

async function submitForm(event) {
  event.preventDefault();
  const button = $('#submit-btn');
  const message = $('#form-msg');
  message.textContent = '';
  message.className = 'form-msg';

  let payload;
  try {
    payload = collectForm();
  } catch (err) {
    message.textContent = err.message;
    message.classList.add('error');
    return;
  }

  button.disabled = true;
  try {
    const { job } = await api('/api/jobs', { method: 'POST', body: payload });
    toast(`Scheduled “${job.name}” — ${job.description}`, 'ok');
    $('#job-form').reset();
    showChosenFile(null);
    $('#run-at').value = defaultRunAt();
    await refresh();
  } catch (err) {
    message.textContent = err.message;
    message.classList.add('error');
  } finally {
    button.disabled = false;
  }
}

/* ---------- rendering ---------- */

function renderJobs(jobs) {
  const host = $('#jobs');
  $('#job-count').textContent = jobs.length
    ? `${jobs.filter((j) => j.enabled).length} active of ${jobs.length}`
    : '';

  if (!jobs.length) {
    host.innerHTML = '<p class="empty">No scheduled prints yet. Add one on the left.</p>';
    return;
  }

  host.innerHTML = '';
  for (const job of jobs) {
    const card = document.createElement('div');
    card.className = 'job' + (job.enabled ? '' : ' paused');

    const status = job.last_status || '';
    const statusPill = status ? `<span class="pill ${status}">${status}</span>` : '';
    const pausedPill = job.enabled ? '' : '<span class="pill">paused</span>';
    const missingPill = job.file_missing ? '<span class="pill error">file missing</span>' : '';
    const livePill = job.source_mode === 'live' ? '<span class="pill">live file</span>' : '';
    const copies = job.copies > 1 ? ` · ${job.copies} copies` : '';
    const nextLine = job.enabled && job.next_run
      ? `<span class="pill next">next ${whenText(job.next_run)}</span>`
      : '';

    card.innerHTML = `
      <div class="job-main">
        <div class="job-title"><strong></strong>${pausedPill}${statusPill}${missingPill}${livePill}</div>
        <div class="job-meta">${job.description}${copies} · ${job.printer || 'default printer'}</div>
        <div class="job-file"></div>
        <div class="job-last">${nextLine}</div>
      </div>
      <div class="job-actions">
        <button type="button" class="ghost" data-act="run">Print now</button>
        <button type="button" class="ghost" data-act="toggle">${job.enabled ? 'Pause' : 'Resume'}</button>
        <button type="button" class="ghost danger" data-act="delete">Delete</button>
      </div>`;

    // textContent, not innerHTML: names and paths are user input.
    card.querySelector('.job-title strong').textContent = job.name;
    card.querySelector('.job-file').textContent = job.file_name;
    if (job.last_message) {
      const note = document.createElement('div');
      note.className = 'job-file';
      note.textContent = `last: ${job.last_message}`;
      card.querySelector('.job-main').append(note);
    }

    card.querySelector('[data-act=run]').addEventListener('click', () => runNow(job));
    card.querySelector('[data-act=toggle]').addEventListener('click', () => toggleJob(job));
    card.querySelector('[data-act=delete]').addEventListener('click', () => deleteJob(job));
    host.append(card);
  }
}

function renderRuns(runs) {
  const host = $('#runs');
  if (!runs.length) {
    host.innerHTML = '<p class="empty">Nothing has printed yet.</p>';
    return;
  }
  host.innerHTML = '';
  for (const run of runs) {
    const row = document.createElement('div');
    row.className = 'run';
    row.innerHTML = `<span class="run-when">${shortStamp(run.ran_at)}</span>
                     <span class="pill ${run.status}">${run.status}</span>
                     <span class="run-name"></span><span class="run-msg"></span>`;
    row.querySelector('.run-name').textContent = run.job_name;
    row.querySelector('.run-msg').textContent = run.message;
    host.append(row);
  }
}

function renderPrinters(printers) {
  const select = $('#printer');
  if (select.dataset.filled === '1') return;
  for (const printer of printers) {
    const option = document.createElement('option');
    option.value = printer.name;
    option.textContent = printer.name + (printer.is_default ? ' (default)' : '');
    select.append(option);
  }
  select.dataset.filled = '1';
}

function setStatus(kind, text) {
  const pill = $('#status-pill');
  pill.className = `status ${kind}`;
  $('#status-text').textContent = text;
}

/* ---------- actions ---------- */

async function runNow(job) {
  try {
    const result = await api(`/api/jobs/${job.id}/run`, { method: 'POST' });
    toast(`${job.name}: ${result.message}`, result.status === 'ok' ? 'ok' : '');
  } catch (err) {
    toast(`${job.name}: ${err.message}`, 'error');
  }
  refresh();
}

async function toggleJob(job) {
  try {
    await api(`/api/jobs/${job.id}/toggle`, { method: 'POST' });
  } catch (err) {
    toast(err.message, 'error');
  }
  refresh();
}

async function deleteJob(job) {
  if (!confirm(`Delete “${job.name}”? This removes the schedule, not anything already printed.`)) return;
  try {
    await api(`/api/jobs/${job.id}`, { method: 'DELETE' });
    toast(`Deleted “${job.name}”.`);
  } catch (err) {
    toast(err.message, 'error');
  }
  refresh();
}

async function refresh() {
  try {
    const data = await api('/api/state');
    renderPrinters(data.printers);
    renderJobs(data.jobs);
    renderRuns(data.runs);
    const queued = data.queue.length;
    setStatus('live', queued ? `${queued} in the printer queue` : 'scheduler running');
  } catch (err) {
    setStatus('down', 'not connected');
  }
}

/* ---------- boot ---------- */

function init() {
  buildDayPicker();
  $('#run-at').value = defaultRunAt();

  $$('#kind-picker .seg').forEach((b) => b.addEventListener('click', () => selectKind(b.dataset.kind)));
  $$('.tab').forEach((b) => b.addEventListener('click', () => selectSource(b.dataset.source)));
  $('#job-form').addEventListener('submit', submitForm);
  $('#refresh-btn').addEventListener('click', refresh);
  $('#file-input').addEventListener('change', (e) => showChosenFile(e.target.files[0] || null));

  const zone = $('#dropzone');
  ['dragenter', 'dragover'].forEach((type) =>
    zone.addEventListener(type, (e) => { e.preventDefault(); zone.classList.add('over'); }));
  ['dragleave', 'drop'].forEach((type) =>
    zone.addEventListener(type, (e) => { e.preventDefault(); zone.classList.remove('over'); }));
  zone.addEventListener('drop', (e) => {
    const file = e.dataTransfer.files[0];
    if (file) { $('#file-input').files = e.dataTransfer.files; showChosenFile(file); }
  });

  refresh();
  setInterval(refresh, 10000);
}

document.addEventListener('DOMContentLoaded', init);
