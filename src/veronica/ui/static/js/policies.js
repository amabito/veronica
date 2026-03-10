// policies.js -- Veronica Policy Editor logic

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

let allPolicies = [];
let currentPolicy = null;

function formatDate(ts) {
  if (!ts) return '--';
  return new Date(ts * 1000).toLocaleDateString() + ' ' + new Date(ts * 1000).toLocaleTimeString();
}

function decisionBadge(decision) {
  const cls = ['halt', 'degrade', 'allow', 'queue', 'retry'].includes(decision)
    ? `badge-${decision}`
    : 'badge-default';
  return `<span class="badge ${cls}">${decision}</span>`;
}

function renderPolicyList(policies) {
  const tbody = document.getElementById('policy-tbody');
  if (!policies || policies.length === 0) {
    tbody.innerHTML = '<tr><td colspan="5" class="empty-state">No policies found</td></tr>';
    return;
  }
  tbody.innerHTML = policies.map(p => `
    <tr class="policy-row" data-chain="${p.chain_id}" style="cursor:pointer;">
      <td class="mono">${p.chain_id}</td>
      <td>${decisionBadge(p.on_exceed)}</td>
      <td class="mono">$${(p.ceiling_usd || 0).toFixed(4)}</td>
      <td>${p.priority}</td>
      <td><button class="btn btn-sm btn-primary" data-chain="${p.chain_id}">Edit</button></td>
    </tr>
  `).join('');

  tbody.querySelectorAll('.btn-primary').forEach(btn => {
    btn.addEventListener('click', () => selectPolicy(btn.dataset.chain));
  });
}

async function selectPolicy(chainId) {
  try {
    const resp = await fetch(`/policies/${chainId}`, { headers: apiHeaders() });
    if (!resp.ok) {
      showToast('Failed to load policy: ' + resp.status, 'error');
      return;
    }
    currentPolicy = await resp.json();
    renderEditor(currentPolicy);
    document.getElementById('editor-panel').style.display = 'block';
    // highlight selected row
    document.querySelectorAll('.policy-row').forEach(r => r.classList.remove('selected'));
    const row = document.querySelector(`.policy-row[data-chain="${chainId}"]`);
    if (row) row.classList.add('selected');
  } catch (err) {
    showToast('Error: ' + err.message, 'error');
  }
}

function renderEditor(policy) {
  document.getElementById('editor-title').textContent = 'Edit: ' + policy.chain_id;
  document.getElementById('field-ceiling-usd').value = policy.ceiling_usd ?? '';
  document.getElementById('field-on-exceed').value = policy.on_exceed ?? 'halt';
  document.getElementById('field-ceiling-tokens').value = policy.ceiling_tokens_out ?? '';
  document.getElementById('field-ceiling-steps').value = policy.ceiling_steps ?? '';
  document.getElementById('field-fallback-model').value = policy.fallback_model ?? '';
  document.getElementById('field-timeout-ms').value = policy.timeout_ms ?? '';
  document.getElementById('field-priority').value = policy.priority ?? 50;
  document.getElementById('field-version').value = policy.version ?? 0;
}

function validateForm() {
  const ceilingUsd = parseFloat(document.getElementById('field-ceiling-usd').value);
  if (isNaN(ceilingUsd) || ceilingUsd < 0) {
    showToast('ceiling_usd must be a non-negative number', 'error');
    return false;
  }
  const priority = parseInt(document.getElementById('field-priority').value);
  if (isNaN(priority) || priority < 0 || priority > 100) {
    showToast('priority must be 0-100', 'error');
    return false;
  }
  return true;
}

function buildUpdatePayload() {
  const payload = {
    current_version: parseInt(document.getElementById('field-version').value) || 0,
    ceiling_usd: parseFloat(document.getElementById('field-ceiling-usd').value) || undefined,
    on_exceed: document.getElementById('field-on-exceed').value || undefined,
    priority: parseInt(document.getElementById('field-priority').value) || undefined,
  };

  const tokens = document.getElementById('field-ceiling-tokens').value.trim();
  if (tokens) payload.ceiling_tokens_out = parseInt(tokens);

  const steps = document.getElementById('field-ceiling-steps').value.trim();
  if (steps) payload.ceiling_steps = parseInt(steps);

  const model = document.getElementById('field-fallback-model').value.trim();
  if (model) payload.fallback_model = model;

  const timeout = document.getElementById('field-timeout-ms').value.trim();
  if (timeout) payload.timeout_ms = parseInt(timeout);

  // Remove undefined keys
  return Object.fromEntries(Object.entries(payload).filter(([, v]) => v !== undefined));
}

async function savePolicy() {
  if (!currentPolicy) return;
  if (!validateForm()) return;

  const saveBtn = document.getElementById('save-btn');
  saveBtn.disabled = true;
  saveBtn.textContent = 'Saving...';

  const payload = buildUpdatePayload();

  try {
    const resp = await fetch(`/policies/${currentPolicy.chain_id}`, {
      method: 'PUT',
      headers: apiHeaders(),
      body: JSON.stringify(payload),
    });

    if (resp.ok) {
      currentPolicy = await resp.json();
      document.getElementById('field-version').value = currentPolicy.version;
      showToast('Policy saved', 'success');
      fetchPolicies(); // refresh list
    } else if (resp.status === 409) {
      showToast('Conflict: policy was updated elsewhere. Refresh and try again.', 'error');
    } else if (resp.status === 422) {
      const data = await resp.json();
      showToast('Validation error: ' + (data.detail || 'unknown'), 'error');
    } else {
      showToast('Save failed: ' + resp.status, 'error');
    }
  } catch (err) {
    showToast('Network error: ' + err.message, 'error');
  } finally {
    saveBtn.disabled = false;
    saveBtn.textContent = 'Save';
  }
}

async function fetchPolicies(page = 1) {
  const key = getApiKey();
  if (!key) return;

  try {
    const resp = await fetch(`/policies?page=${page}&per_page=50`, { headers: apiHeaders() });
    if (!resp.ok) {
      if (resp.status === 401) showToast('Invalid API key', 'error');
      return;
    }
    const data = await resp.json();
    allPolicies = data.items || [];
    renderPolicyList(allPolicies);

    const totalEl = document.getElementById('policy-total');
    if (totalEl) totalEl.textContent = data.total + ' policies';
  } catch (err) {
    showToast('Failed to load policies: ' + err.message, 'error');
  }
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
  fetchPolicies();
}

document.addEventListener('DOMContentLoaded', () => {
  const key = getApiKey();
  if (!key) {
    showApiKeyModal();
  } else {
    fetchPolicies();
  }

  const saveBtn = document.getElementById('apikey-save');
  if (saveBtn) {
    saveBtn.addEventListener('click', () => saveApiKey());
  }

  const apikeyInput = document.getElementById('apikey-input');
  if (apikeyInput) {
    apikeyInput.addEventListener('keydown', e => {
      if (e.key === 'Enter') saveBtn && saveBtn.click();
    });
  }

  const policySaveBtn = document.getElementById('save-btn');
  if (policySaveBtn) {
    policySaveBtn.addEventListener('click', savePolicy);
  }

  const cancelBtn = document.getElementById('cancel-btn');
  if (cancelBtn) {
    cancelBtn.addEventListener('click', () => {
      document.getElementById('editor-panel').style.display = 'none';
      currentPolicy = null;
      document.querySelectorAll('.policy-row').forEach(r => r.classList.remove('selected'));
    });
  }
});
