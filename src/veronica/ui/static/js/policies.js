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

function escHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

let allPolicies = [];
let currentPolicy = null;

// Diff tracking: snapshot of form values when policy was loaded
let originalValues = null;

// Field IDs to watch for diff
const DIFF_FIELDS = [
  'field-ceiling-usd',
  'field-on-exceed',
  'field-priority',
  'field-ceiling-tokens',
  'field-ceiling-steps',
  'field-fallback-model',
  'field-timeout-ms',
];

function captureFormValues() {
  const values = {};
  DIFF_FIELDS.forEach(id => {
    values[id] = document.getElementById(id).value;
  });
  return values;
}

function updateDiffHighlights() {
  if (!originalValues) return;
  const current = captureFormValues();
  let changedCount = 0;
  DIFF_FIELDS.forEach(id => {
    const el = document.getElementById(id);
    if (!el) return;
    const changed = current[id] !== originalValues[id];
    if (changed) changedCount++;
    el.classList.toggle('field-changed', changed);
    // Highlight parent form-group
    const grp = el.closest('.form-group');
    if (grp) grp.classList.toggle('form-group-changed', changed);
  });
  const summary = document.getElementById('diff-summary');
  if (changedCount > 0) {
    summary.textContent = changedCount + (changedCount === 1 ? ' field changed' : ' fields changed');
    summary.style.display = 'block';
  } else {
    summary.style.display = 'none';
  }
}

function clearDiffState() {
  originalValues = null;
  DIFF_FIELDS.forEach(id => {
    const el = document.getElementById(id);
    if (!el) return;
    el.classList.remove('field-changed');
    const grp = el.closest('.form-group');
    if (grp) grp.classList.remove('form-group-changed');
  });
  const summary = document.getElementById('diff-summary');
  if (summary) summary.style.display = 'none';
}

function formatDate(ts) {
  if (!ts) return '--';
  return new Date(ts * 1000).toLocaleDateString() + ' ' + new Date(ts * 1000).toLocaleTimeString();
}

function decisionBadge(decision) {
  const d = (decision || 'unknown').toLowerCase();
  const cls = ['halt', 'degrade', 'allow', 'queue', 'retry'].includes(d)
    ? `badge-${d}`
    : 'badge-default';
  return `<span class="badge ${cls}">${escHtml(d)}</span>`;
}

function renderPolicyList(policies) {
  const tbody = document.getElementById('policy-tbody');
  if (!policies || policies.length === 0) {
    tbody.innerHTML = '<tr><td colspan="5" class="empty-state">No policies found</td></tr>';
    return;
  }
  tbody.innerHTML = policies.map(p => `
    <tr class="policy-row" data-chain="${escHtml(p.chain_id)}" style="cursor:pointer;">
      <td class="mono">${escHtml(p.chain_id)}</td>
      <td>${decisionBadge(p.on_exceed)}</td>
      <td class="mono">$${(p.ceiling_usd || 0).toFixed(4)}</td>
      <td>${p.priority}</td>
      <td><button class="btn btn-sm btn-primary" data-chain="${escHtml(p.chain_id)}">Edit</button></td>
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

  // Show policy_hash (first 8 chars) in editor header
  const hashEl = document.getElementById('editor-policy-hash');
  if (hashEl) {
    if (policy.policy_hash) {
      hashEl.textContent = policy.policy_hash.slice(0, 8);
      hashEl.style.display = 'inline';
    } else {
      hashEl.style.display = 'none';
    }
  }

  // Capture original values for diff tracking
  originalValues = captureFormValues();
  clearDiffState();

  // Hide simulate result on new policy load
  const simResult = document.getElementById('simulate-result');
  if (simResult) simResult.style.display = 'none';
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

function decisionBadgeSimulate(decision) {
  const d = (decision || 'unknown').toLowerCase();
  const cls = ['halt', 'degrade', 'allow', 'queue', 'retry'].includes(d)
    ? `badge-${d}`
    : 'badge-default';
  return `<span class="badge ${cls}">${escHtml(d)}</span>`;
}

async function simulatePolicy() {
  if (!currentPolicy) return;
  if (!validateForm()) return;

  const simBtn = document.getElementById('simulate-btn');
  const simResult = document.getElementById('simulate-result');
  simBtn.disabled = true;
  simBtn.textContent = 'Simulating...';

  // Build policy dict from current form
  const policyDict = {
    chain_id: currentPolicy.chain_id,
    ceiling_usd: parseFloat(document.getElementById('field-ceiling-usd').value) || 0,
    on_exceed: document.getElementById('field-on-exceed').value || 'halt',
    priority: parseInt(document.getElementById('field-priority').value) || 50,
  };

  const tokens = document.getElementById('field-ceiling-tokens').value.trim();
  if (tokens) policyDict.ceiling_tokens_out = parseInt(tokens);

  const steps = document.getElementById('field-ceiling-steps').value.trim();
  if (steps) policyDict.ceiling_steps = parseInt(steps);

  const model = document.getElementById('field-fallback-model').value.trim();
  if (model) policyDict.fallback_model = model;

  const timeout = document.getElementById('field-timeout-ms').value.trim();
  if (timeout) policyDict.timeout_ms = parseInt(timeout);

  try {
    const resp = await fetch('/simulate', {
      method: 'POST',
      headers: apiHeaders(),
      body: JSON.stringify({ policy: policyDict, steps: [] }),
    });

    if (!resp.ok) {
      const data = await resp.json().catch(() => ({}));
      showToast('Simulate failed: ' + (data.detail || resp.status), 'error');
      return;
    }

    const data = await resp.json();

    // Update policy_hash display
    const hashEl = document.getElementById('editor-policy-hash');
    if (hashEl && data.policy_hash) {
      hashEl.textContent = data.policy_hash.slice(0, 8);
      hashEl.style.display = 'inline';
    }

    // Render result panel
    const storeTag = data.store_unchanged
      ? '<span class="sim-tag sim-tag-ok">store_unchanged: true</span>'
      : '<span class="sim-tag sim-tag-warn">store_unchanged: false</span>';

    const stepRows = (data.step_results || []).map((s, i) =>
      `<tr>
        <td>${s.step_index}</td>
        <td>${s.kind || '--'}</td>
        <td>$${(s.cost_usd || 0).toFixed(4)}</td>
        <td>${decisionBadgeSimulate(s.decision)}</td>
        <td style="font-size:11px;color:var(--text-muted)">${escHtml(s.reason || '')}</td>
      </tr>`
    ).join('');

    const stepsSection = stepRows
      ? `<table class="sim-steps-table">
          <thead><tr><th>#</th><th>Kind</th><th>Cost</th><th>Decision</th><th>Reason</th></tr></thead>
          <tbody>${stepRows}</tbody>
        </table>`
      : '<p class="sim-no-steps">No scenario steps -- policy validated successfully</p>';

    simResult.innerHTML = `
      <div class="sim-header">
        ${storeTag}
        <span class="sim-hash">hash: <code>${escHtml((data.policy_hash || '').slice(0, 8))}</code></span>
        <span class="sim-final">${decisionBadgeSimulate(data.final_decision)}</span>
      </div>
      ${stepsSection}
    `;
    simResult.style.display = 'block';
  } catch (err) {
    showToast('Network error: ' + err.message, 'error');
  } finally {
    simBtn.disabled = false;
    simBtn.textContent = 'Simulate';
  }
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
      clearDiffState();
      // Re-snapshot after save
      originalValues = captureFormValues();
      showToast('Policy saved', 'success');
      fetchPolicies(); // refresh list
    } else if (resp.status === 409) {
      showToast('Conflict: policy was updated elsewhere. Refresh and try again.', 'error');
    } else if (resp.status === 403) {
      showToast('Updates disabled: immutable config mode is active', 'error');
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

  const simulateBtn = document.getElementById('simulate-btn');
  if (simulateBtn) {
    simulateBtn.addEventListener('click', simulatePolicy);
  }

  const cancelBtn = document.getElementById('cancel-btn');
  if (cancelBtn) {
    cancelBtn.addEventListener('click', () => {
      document.getElementById('editor-panel').style.display = 'none';
      currentPolicy = null;
      clearDiffState();
      document.querySelectorAll('.policy-row').forEach(r => r.classList.remove('selected'));
    });
  }

  // Diff tracking: listen for changes on all form fields
  DIFF_FIELDS.forEach(id => {
    const el = document.getElementById(id);
    if (el) el.addEventListener('input', updateDiffHighlights);
    if (el) el.addEventListener('change', updateDiffHighlights);
  });
});
