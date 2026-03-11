// dashboard.js -- Veronica Dashboard logic

const API_KEY_STORAGE = 'veronica_api_key';

function getApiKey() {
  return localStorage.getItem(API_KEY_STORAGE) || '';
}

function apiHeaders() {
  return {
    'X-Veronica-Key': getApiKey(),
    'Content-Type': 'application/json',
  };
}

function showToast(message, type = 'success') {
  const container = document.getElementById('toast-container');
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  toast.textContent = message;
  container.appendChild(toast);
  setTimeout(() => toast.remove(), 3500);
}


function formatTime(ts) {
  if (!ts) return '--';
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString();
}

function formatCost(usd) {
  return '$' + (usd || 0).toFixed(4);
}

function decisionBadge(decision) {
  const d = (decision || 'unknown').toLowerCase();
  const cls = ['halt', 'degrade', 'allow', 'queue', 'retry'].includes(d)
    ? `badge-${d}`
    : 'badge-default';
  return `<span class="badge ${cls}">${escHtml(d)}</span>`;
}

function circuitStateBadge(state) {
  if (!state || state === 'null' || state === 'None') return 'N/A';
  const upper = state.toUpperCase();
  if (upper === 'CLOSED') return 'CLOSED';
  if (upper === 'HALF_OPEN') return 'HALF_OPEN';
  if (upper === 'OPEN') return 'OPEN';
  return escHtml(state);
}

function updateStats(events, activeChains, circuitState) {
  const totalCost = events.reduce((s, e) => s + (e.cost_usd || 0), 0);
  const haltCount = events.filter(e => e.decision === 'halt').length;
  const degradeCount = events.filter(e => e.decision === 'degrade').length;

  document.getElementById('stat-chains').textContent = activeChains !== null ? activeChains : '--';
  document.getElementById('stat-cost').textContent = '$' + totalCost.toFixed(2);
  document.getElementById('stat-halts').textContent = haltCount;
  document.getElementById('stat-degrades').textContent = degradeCount;

  const circuitEl = document.getElementById('stat-circuit');
  if (circuitEl) {
    const label = circuitStateBadge(circuitState);
    circuitEl.textContent = label;
    circuitEl.className = 'stat-value';
    if (label === 'CLOSED') circuitEl.classList.add('success');
    else if (label === 'HALF_OPEN') circuitEl.classList.add('warning');
    else if (label === 'OPEN') circuitEl.classList.add('danger');
  }
}

function updateTable(events) {
  const tbody = document.getElementById('events-tbody');
  if (!events || events.length === 0) {
    tbody.innerHTML = '<tr><td colspan="6" class="empty-state">No events yet</td></tr>';
    return;
  }

  tbody.innerHTML = events.map(e => `
    <tr>
      <td class="mono">${formatTime(e.timestamp)}</td>
      <td class="mono">${escHtml(e.chain_id || '--')}</td>
      <td>${escHtml(e.operation_name || '--')}</td>
      <td>${decisionBadge(e.decision)}</td>
      <td class="mono">${formatCost(e.cost_usd)}</td>
      <td class="mono">${(e.tokens || 0).toLocaleString()}</td>
    </tr>
  `).join('');
}

function updateRefreshTime() {
  const el = document.getElementById('last-refresh');
  if (el) el.textContent = 'Updated ' + new Date().toLocaleTimeString();
}

async function fetchData() {
  const key = getApiKey();
  if (!key) return;

  let events = [];
  let activeChains = null;
  let circuitState = null;

  try {
    const evResp = await fetch('/events?limit=50', { headers: apiHeaders() });
    if (evResp.ok) {
      const data = await evResp.json();
      events = Array.isArray(data) ? data : (data.items || []);
    } else if (evResp.status === 401) {
      showToast('Invalid API key', 'error');
      return;
    }
  } catch (err) {
    showToast('Failed to fetch events: ' + err.message, 'error');
  }

  try {
    // Fetch active_chains and circuit_state from /health (no auth required)
    const healthResp = await fetch('/health');
    if (healthResp.ok) {
      const data = await healthResp.json();
      const stats = data.stats || {};
      activeChains = typeof stats.active_chains === 'number' ? stats.active_chains : null;
      circuitState = stats.circuit_state || null;
    }
  } catch (err) {
    // non-critical
  }

  updateStats(events, activeChains, circuitState);
  updateTable(events);
  updateRefreshTime();
}

function showApiKeyModal() {
  const overlay = document.getElementById('apikey-modal');
  overlay.style.display = 'flex';
}

function hideApiKeyModal() {
  const overlay = document.getElementById('apikey-modal');
  overlay.style.display = 'none';
}

function saveApiKey() {
  const input = document.getElementById('apikey-input');
  const key = input.value.trim();
  if (!key) {
    showToast('API key cannot be empty', 'error');
    return;
  }
  localStorage.setItem(API_KEY_STORAGE, key);
  hideApiKeyModal();
  fetchData();
}

document.addEventListener('DOMContentLoaded', () => {
  const key = getApiKey();
  let refreshTimer = null;
  if (!key) {
    showApiKeyModal();
  } else {
    fetchData();
    refreshTimer = setInterval(fetchData, 5000);
  }

  const saveBtn = document.getElementById('apikey-save');
  if (saveBtn) {
    saveBtn.addEventListener('click', () => {
      saveApiKey();
      if (!refreshTimer) refreshTimer = setInterval(fetchData, 5000);
    });
  }

  const apikeyInput = document.getElementById('apikey-input');
  if (apikeyInput) {
    apikeyInput.addEventListener('keydown', e => {
      if (e.key === 'Enter') saveBtn && saveBtn.click();
    });
  }

  window.addEventListener('beforeunload', () => {
    if (refreshTimer) clearInterval(refreshTimer);
  });
});
