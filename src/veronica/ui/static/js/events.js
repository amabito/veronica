// events.js -- Veronica Event Log page logic
// API: GET /events?offset=N&limit=N&decision=X&chain_id=X&since=N&until=N

'use strict';

const API_KEY_STORAGE = 'veronica_api_key';

function getApiKey() {
  return localStorage.getItem(API_KEY_STORAGE) || '';
}

function apiHeaders() {
  return { 'X-Veronica-Key': getApiKey() };
}

function showToast(message, type = 'success') {
  const container = document.getElementById('toast-container');
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  toast.textContent = message;
  container.appendChild(toast);
  setTimeout(() => toast.remove(), 3500);
}

function escHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function formatTime(ts) {
  if (!ts) return '--';
  const d = new Date(ts * 1000);
  const pad = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())} `
       + `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

function formatCost(usd) {
  if (usd == null) return '--';
  if (usd === 0) return '$0.0000';
  if (usd < 0.001) return '<$0.001';
  return '$' + usd.toFixed(4);
}

// Supported classes: badge-allow badge-halt badge-degrade badge-retry badge-queue badge-quarantine badge-unknown badge-default
function decisionBadge(decision) {
  const d = (decision || 'unknown').toLowerCase();
  const known = ['allow', 'halt', 'degrade', 'retry', 'queue', 'quarantine', 'unknown'];
  const cls = known.includes(d) ? `badge-${d}` : 'badge-default';
  return `<span class="badge ${cls}">${escHtml(d)}</span>`;
}

function truncate(s, len) {
  if (!s) return '--';
  return s.length > len ? s.slice(0, len) + '...' : s;
}

// ---- State ----
const state = {
  offset: 0,
  limit: 100,
  total: 0,
  decision: '',
  chain_id: '',
  since: '',
  until: '',
  loading: false,
  refreshTimer: null,
};

// ---- DOM refs ----
const tbody        = document.getElementById('events-tbody');
const eventCount   = document.getElementById('event-count');
const statusMsg    = document.getElementById('status-msg');
const truncWarn    = document.getElementById('truncated-warn');
const pageInfo     = document.getElementById('page-info');
const pageNum      = document.getElementById('page-num');
const btnPrev      = document.getElementById('btn-prev');
const btnNext      = document.getElementById('btn-next');
const btnApply     = document.getElementById('btn-apply');
const btnReset     = document.getElementById('btn-reset');
const selDecision  = document.getElementById('filter-decision');
const inChain      = document.getElementById('filter-chain');
const inSince      = document.getElementById('filter-since');
const inUntil      = document.getElementById('filter-until');
const selLimit     = document.getElementById('filter-limit');
const autoRefresh  = document.getElementById('auto-refresh');

// ---- API Key modal ----
(function initApiKey() {
  const modal  = document.getElementById('apikey-modal');
  const input  = document.getElementById('apikey-input');
  const saveBtn = document.getElementById('apikey-save');

  function maybeShow() {
    if (!getApiKey()) modal.style.display = 'flex';
  }

  saveBtn.addEventListener('click', () => {
    const key = input.value.trim();
    if (key) {
      localStorage.setItem(API_KEY_STORAGE, key);
      modal.style.display = 'none';
      fetchEvents();
    }
  });

  input.addEventListener('keydown', e => { if (e.key === 'Enter') saveBtn.click(); });

  maybeShow();
})();

// ---- Build query string ----
function buildParams() {
  const p = new URLSearchParams();
  p.set('offset', state.offset);
  p.set('limit', state.limit);
  if (state.decision) p.set('decision', state.decision);
  if (state.chain_id) p.set('chain_id', state.chain_id);
  if (state.since) {
    const ts = Math.floor(new Date(state.since).getTime() / 1000);
    if (!isNaN(ts)) p.set('since', ts);
  }
  if (state.until) {
    const ts = Math.floor(new Date(state.until).getTime() / 1000);
    if (!isNaN(ts)) p.set('until', ts);
  }
  return p;
}

// ---- Render rows ----
function renderRows(items) {
  if (!items.length) {
    tbody.innerHTML = '<tr><td colspan="8" class="empty-state">No events match the current filters.</td></tr>';
    return;
  }

  tbody.innerHTML = items.map(e => {
    const chain = escHtml(e.chain_id || '--');
    const op    = escHtml(e.operation_name || '--');
    return `<tr data-chain="${chain}" data-step="${escHtml(e.step_id || '')}">
      <td class="td-ts">${formatTime(e.timestamp)}</td>
      <td class="td-chain" title="${chain}">${chain}</td>
      <td title="${op}" style="max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${op}</td>
      <td>${decisionBadge(e.decision)}</td>
      <td class="td-cost">${formatCost(e.cost_usd)}</td>
      <td class="td-tokens">${(e.tokens || 0).toLocaleString()}</td>
      <td class="td-hash mono" title="${escHtml(e.policy_hash || '')}">${truncate(e.policy_hash, 8)}</td>
      <td class="td-hash mono" title="${escHtml(e.audit_id || '')}">${truncate(e.audit_id, 8)}</td>
    </tr>`;
  }).join('');

  // Row click -> incident detail
  tbody.querySelectorAll('tr[data-step]').forEach(row => {
    row.addEventListener('click', () => {
      const cid = row.dataset.chain;
      const sid = row.dataset.step;
      if (cid && cid !== '--') {
        window.location.href =
          `/ui/incident?chain_id=${encodeURIComponent(cid)}&step_id=${encodeURIComponent(sid)}`;
      }
    });
  });
}

// ---- Render pagination ----
function renderPagination() {
  const page = Math.floor(state.offset / state.limit) + 1;
  const total = state.total;
  const pages = total ? Math.ceil(total / state.limit) : 1;
  const start = total ? state.offset + 1 : 0;
  const end   = Math.min(state.offset + state.limit, total);

  pageNum.textContent  = `Page ${page} / ${pages}`;
  pageInfo.textContent = total ? `Showing ${start}-${end} of ${total}` : 'No events';
  eventCount.textContent = `${(total || 0).toLocaleString()} events`;

  btnPrev.disabled = state.offset === 0 || state.loading;
  btnNext.disabled = state.offset + state.limit >= total || state.loading;
}

// ---- Fetch ----
async function fetchEvents() {
  if (state.loading) return;
  state.loading = true;
  statusMsg.textContent = 'Loading...';
  truncWarn.style.display = 'none';

  try {
    const resp = await fetch(`/events?${buildParams()}`, { headers: apiHeaders() });

    if (resp.status === 401) {
      document.getElementById('apikey-modal').style.display = 'flex';
      statusMsg.textContent = '';
      return;
    }
    if (!resp.ok) {
      const body = await resp.json().catch(() => ({}));
      throw new Error(body.detail || `HTTP ${resp.status}`);
    }

    const data = await resp.json();
    state.total = data.total || 0;
    renderRows(data.items || []);
    renderPagination();

    if (data.truncated) truncWarn.style.display = 'block';
    statusMsg.textContent = `Updated ${new Date().toLocaleTimeString()}`;
  } catch (err) {
    statusMsg.textContent = `Error: ${err.message}`;
    tbody.innerHTML = `<tr><td colspan="8" class="empty-state">Failed to load: ${escHtml(err.message)}</td></tr>`;
    showToast(err.message, 'error');
  } finally {
    state.loading = false;
    renderPagination();
  }
}

// ---- Filters ----
function applyFilters() {
  state.offset   = 0;
  state.decision = selDecision.value;
  state.chain_id = inChain.value.trim();
  state.since    = inSince.value;
  state.until    = inUntil.value;
  state.limit    = parseInt(selLimit.value, 10) || 100;
  fetchEvents();
}

function resetFilters() {
  selDecision.value = '';
  inChain.value     = '';
  inSince.value     = '';
  inUntil.value     = '';
  selLimit.value    = '100';
  state.offset   = 0;
  state.decision = '';
  state.chain_id = '';
  state.since    = '';
  state.until    = '';
  state.limit    = 100;
  fetchEvents();
}

btnApply.addEventListener('click', applyFilters);
btnReset.addEventListener('click', resetFilters);
selDecision.addEventListener('change', applyFilters);
selLimit.addEventListener('change', applyFilters);
[inChain, inSince, inUntil].forEach(el => {
  el.addEventListener('keydown', e => { if (e.key === 'Enter') applyFilters(); });
});

// ---- Pagination ----
btnPrev.addEventListener('click', () => {
  if (state.offset > 0) {
    state.offset = Math.max(0, state.offset - state.limit);
    fetchEvents();
  }
});
btnNext.addEventListener('click', () => {
  if (state.offset + state.limit < state.total) {
    state.offset += state.limit;
    fetchEvents();
  }
});

// ---- Auto-refresh ----
autoRefresh.addEventListener('change', () => {
  if (autoRefresh.checked) {
    state.refreshTimer = setInterval(fetchEvents, 30000);
  } else {
    clearInterval(state.refreshTimer);
    state.refreshTimer = null;
  }
});

// ---- Pre-fill from URL params ----
(function readUrl() {
  const p = new URLSearchParams(window.location.search);
  const cid = p.get('chain_id');
  const dec = p.get('decision');
  if (cid) { inChain.value = cid; state.chain_id = cid; }
  if (dec && selDecision.querySelector(`option[value="${dec}"]`)) {
    selDecision.value = dec; state.decision = dec;
  }
})();

// ---- Init ----
fetchEvents();
