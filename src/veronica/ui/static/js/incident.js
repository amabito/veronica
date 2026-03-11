// incident.js -- Veronica Incident Detail page logic
// URL params: ?chain_id=X&step_id=Y
// API: GET /events?chain_id=X&limit=1000

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

function formatDuration(ms) {
  if (ms == null) return '--';
  return ms >= 1000 ? `${(ms / 1000).toFixed(2)}s` : `${Math.round(ms)}ms`;
}

// Supported classes: badge-allow badge-halt badge-degrade badge-retry badge-queue badge-quarantine badge-unknown badge-default
function decisionBadge(decision) {
  const d = (decision || 'unknown').toLowerCase();
  const known = ['allow', 'halt', 'degrade', 'retry', 'queue', 'quarantine', 'unknown'];
  const cls = known.includes(d) ? `badge-${d}` : 'badge-default';
  return `<span class="badge ${cls}">${escHtml(d)}</span>`;
}

function decisionCssClass(d) {
  const known = ['allow', 'halt', 'degrade', 'retry', 'queue', 'quarantine', 'unknown'];
  return known.includes((d || '').toLowerCase()) ? d.toLowerCase() : 'unknown';
}

// ---- URL params ----
const urlParams  = new URLSearchParams(window.location.search);
const chainId    = urlParams.get('chain_id') || '';
const focalStepId = urlParams.get('step_id') || '';

// ---- DOM refs ----
const content    = document.getElementById('content');
const chainLabel = document.getElementById('chain-id-display');
const backLink   = document.getElementById('back-link');

if (chainId) {
  chainLabel.textContent = chainId;
  backLink.href = `/ui/events?chain_id=${encodeURIComponent(chainId)}`;
}

// ---- API Key modal ----
(function initApiKey() {
  const modal   = document.getElementById('apikey-modal');
  const input   = document.getElementById('apikey-input');
  const saveBtn = document.getElementById('apikey-save');

  if (!getApiKey()) modal.style.display = 'flex';

  saveBtn.addEventListener('click', () => {
    const key = input.value.trim();
    if (key) {
      localStorage.setItem(API_KEY_STORAGE, key);
      modal.style.display = 'none';
      fetchChain();
    }
  });

  input.addEventListener('keydown', e => { if (e.key === 'Enter') saveBtn.click(); });
})();

// ---- Find root cause ----
function findRootCause(items) {
  return [...items]
    .sort((a, b) => a.timestamp - b.timestamp)
    .find(it => it.decision === 'halt' || it.decision === 'degrade') || null;
}

// ---- Compute stats ----
function computeStats(items) {
  const counts = {};
  let totalCost = 0;
  let totalTokens = 0;
  items.forEach(it => {
    const d = it.decision || 'unknown';
    counts[d] = (counts[d] || 0) + 1;
    totalCost   += it.cost_usd || 0;
    totalTokens += it.tokens   || 0;
  });
  return { counts, totalCost, totalTokens, total: items.length };
}

// ---- Render summary strip ----
function renderSummary(stats, rootCause) {
  const hasIncident = stats.counts['halt'] || stats.counts['degrade'];
  const statusBadge = hasIncident
    ? decisionBadge(rootCause ? rootCause.decision : 'halt')
    : decisionBadge('allow');

  return `<div class="summary-strip">
    <div class="stat-card">
      <div class="stat-label">Total Steps</div>
      <div class="stat-value primary">${stats.total}</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">Total Cost</div>
      <div class="stat-value">${formatCost(stats.totalCost)}</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">Total Tokens</div>
      <div class="stat-value">${stats.totalTokens.toLocaleString()}</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">Chain Status</div>
      <div class="stat-value" style="font-size:16px;padding-top:4px;">${statusBadge}</div>
    </div>
    ${rootCause ? `
    <div class="stat-card">
      <div class="stat-label">Root Cause Step</div>
      <div class="stat-value mono" style="font-size:13px;">${escHtml(rootCause.step_id)}</div>
    </div>` : ''}
  </div>`;
}

// ---- Render decision breakdown ----
function renderBreakdown(stats) {
  const order  = ['halt', 'degrade', 'allow', 'retry', 'queue', 'quarantine', 'unknown'];
  const colors = {
    allow: 'var(--success)', halt: 'var(--danger)', degrade: 'var(--warning)',
    retry: 'var(--primary)', queue: 'var(--info)', quarantine: '#a855f7',
    unknown: 'var(--text-muted)',
  };
  const rows = order.filter(d => stats.counts[d]).map(d => {
    const pct = stats.total ? Math.round((stats.counts[d] / stats.total) * 100) : 0;
    return `<div class="decision-row">
      <div style="width:70px;">${decisionBadge(d)}</div>
      <div class="bar-bg"><div class="bar-fill" style="width:${pct}%;background:${colors[d] || 'var(--text-muted)'}"></div></div>
      <div class="cnt">${stats.counts[d]}</div>
    </div>`;
  }).join('');

  if (!rows) return '';
  return `<div class="card sidebar-card">
    <div class="card-header"><span class="card-title">Decision Breakdown</span></div>
    <div class="card-body">${rows}</div>
  </div>`;
}

// ---- Render policy sidebar ----
function renderPolicySidebar(items) {
  const hashes = [...new Set(items.map(it => it.policy_hash).filter(Boolean))];
  if (!hashes.length) return '';

  const rows = hashes.map(h => `
    <div class="pi-row">
      <div class="pi-label">Policy Hash</div>
      <div class="pi-value" title="${escHtml(h)}">${escHtml(h)}</div>
    </div>`).join('');

  return `<div class="card sidebar-card">
    <div class="card-header"><span class="card-title">Policy Details</span></div>
    <div class="card-body">${rows}</div>
  </div>`;
}

// ---- Render timeline ----
function renderTimeline(items, rootCause) {
  const sorted = [...items].sort((a, b) => a.timestamp - b.timestamp);

  const tls = sorted.map(item => {
    const d = decisionCssClass(item.decision);
    const isRoot  = rootCause && item.step_id === rootCause.step_id;
    const isFocal = item.step_id === focalStepId;
    const isBad   = d === 'halt' || d === 'degrade';
    const cardCls = [isBad ? d : '', isFocal ? 'focal' : ''].filter(Boolean).join(' ');

    const chain = escHtml(item.chain_id || '--');
    const op    = escHtml(item.operation_name || '--');

    return `<div class="tl-item" id="step-${escHtml(item.step_id || '')}">
      <div class="tl-dot ${d}"></div>
      <div class="tl-card ${cardCls}">
        <div class="tl-header">
          <span class="tl-ts">${formatTime(item.timestamp)}</span>
          <span class="tl-op" title="${op}">${op}</span>
          ${decisionBadge(item.decision)}
          ${isRoot && isBad ? '<span class="root-cause-tag">[!] Root Cause</span>' : ''}
        </div>
        <div class="tl-meta">
          <span>Step: <b class="mono">${escHtml(item.step_id || '--')}</b></span>
          <span>Cost: <b>${formatCost(item.cost_usd)}</b></span>
          <span>Tokens: <b>${(item.tokens || 0).toLocaleString()}</b></span>
          <span>Duration: <b>${formatDuration(item.duration_ms)}</b></span>
          <span>Policy: <b class="mono" title="${escHtml(item.policy_hash || '')}">${escHtml((item.policy_hash || '--').slice(0, 8))}</b></span>
        </div>
      </div>
    </div>`;
  }).join('');

  return `<div class="card">
    <div class="card-header">
      <span class="card-title">Event Timeline</span>
      <span class="refresh-indicator">${sorted.length} step${sorted.length !== 1 ? 's' : ''}, oldest first</span>
    </div>
    <div class="card-body" style="padding:16px 20px;">
      <div class="timeline-wrap">${tls || '<div class="empty-state">No events</div>'}</div>
    </div>
  </div>`;
}

// ---- Render full page ----
function renderPage(items) {
  if (!items.length) {
    content.innerHTML = `<div class="empty-state">No events found for chain <span class="mono">${escHtml(chainId)}</span>.</div>`;
    return;
  }

  const stats = computeStats(items);
  const rootCause = findRootCause(items);

  content.innerHTML = `
    ${renderSummary(stats, rootCause)}
    <div class="incident-grid">
      <div>${renderTimeline(items, rootCause)}</div>
      <div>
        ${renderBreakdown(stats)}
        ${renderPolicySidebar(items)}
      </div>
    </div>`;

  // Scroll focal step into view
  if (focalStepId) {
    const el = document.getElementById(`step-${escHtml(focalStepId)}`);
    if (el) setTimeout(() => el.scrollIntoView({ behavior: 'smooth', block: 'center' }), 100);
  }
}

// ---- Fetch ----
async function fetchChain() {
  if (!chainId) {
    content.innerHTML = `<div class="empty-state">No chain_id specified in URL.</div>`;
    return;
  }

  const params = new URLSearchParams({ chain_id: chainId, limit: 1000 });

  try {
    const resp = await fetch(`/events?${params}`, { headers: apiHeaders() });

    if (resp.status === 401) {
      document.getElementById('apikey-modal').style.display = 'flex';
      return;
    }
    if (!resp.ok) {
      const body = await resp.json().catch(() => ({}));
      throw new Error(body.detail || `HTTP ${resp.status}`);
    }

    const data = await resp.json();
    if (data.truncated || data.total > 1000) {
      showToast(`Chain has ${data.total} events; showing first 1000.`, 'error');
    }
    renderPage(data.items || []);
  } catch (err) {
    content.innerHTML = `<div class="empty-state">Failed to load chain: ${escHtml(err.message)}</div>`;
    showToast(err.message, 'error');
  }
}

// ---- Init ----
fetchChain();
