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
  const cls = ['halt', 'degrade', 'allow', 'queue', 'retry'].includes(decision)
    ? `badge-${decision}`
    : 'badge-default';
  return `<span class="badge ${cls}">${decision}</span>`;
}

function updateStats(events, policiesTotal) {
  const totalCost = events.reduce((s, e) => s + (e.cost_usd || 0), 0);
  const haltCount = events.filter(e => e.decision === 'halt').length;
  const degradeCount = events.filter(e => e.decision === 'degrade').length;

  document.getElementById('stat-chains').textContent = policiesTotal;
  document.getElementById('stat-cost').textContent = '$' + totalCost.toFixed(2);
  document.getElementById('stat-halts').textContent = haltCount;
  document.getElementById('stat-degrades').textContent = degradeCount;
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
      <td class="mono">${e.chain_id || '--'}</td>
      <td>${e.operation_name || '--'}</td>
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
  let policiesTotal = 0;

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
    const polResp = await fetch('/policies?per_page=1', { headers: apiHeaders() });
    if (polResp.ok) {
      const data = await polResp.json();
      policiesTotal = data.total || 0;
    }
  } catch (err) {
    // non-critical, proceed with 0
  }

  updateStats(events, policiesTotal);
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
  if (!key) {
    showApiKeyModal();
  } else {
    fetchData();
    setInterval(fetchData, 5000);
  }

  const saveBtn = document.getElementById('apikey-save');
  if (saveBtn) {
    saveBtn.addEventListener('click', () => {
      saveApiKey();
      setInterval(fetchData, 5000);
    });
  }

  const apikeyInput = document.getElementById('apikey-input');
  if (apikeyInput) {
    apikeyInput.addEventListener('keydown', e => {
      if (e.key === 'Enter') saveBtn && saveBtn.click();
    });
  }
});
