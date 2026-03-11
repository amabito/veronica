// replay.js -- Veronica Incident Replay page logic
// API: POST /replay

'use strict';

const API_KEY_STORAGE = 'veronica_api_key';

function getApiKey() {
  return localStorage.getItem(API_KEY_STORAGE) || '';
}

function apiHeaders() {
  return {
    'Content-Type': 'application/json',
    'X-Veronica-Key': getApiKey(),
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


function decisionBadge(decision) {
  const d = (decision || 'unknown').toLowerCase();
  const known = ['allow', 'halt', 'degrade', 'retry', 'queue', 'quarantine', 'unknown'];
  const cls = known.includes(d) ? `badge-${d}` : 'badge-default';
  return `<span class="badge ${cls}">${escHtml(d)}</span>`;
}

// ---- DOM refs ----
const inpChain      = document.getElementById('inp-chain');
const inpFrom       = document.getElementById('inp-from');
const inpTo         = document.getElementById('inp-to');
const inpPolicy     = document.getElementById('inp-policy');
const policyError   = document.getElementById('policy-error');
const btnRun        = document.getElementById('btn-run');
const statusMsg     = document.getElementById('status-msg');
const resultEmpty   = document.getElementById('result-empty');
const resultContent = document.getElementById('result-content');
const resultChain   = document.getElementById('result-chain');
const resultBadge   = document.getElementById('result-changed-badge');
const statsRow      = document.getElementById('stats-row');
const diffTbody     = document.getElementById('diff-tbody');

// ---- API Key modal ----
(function initApiKey() {
  const modal  = document.getElementById('apikey-modal');
  const input  = document.getElementById('apikey-input');
  const saveBtn = document.getElementById('apikey-save');

  saveBtn.addEventListener('click', () => {
    const key = input.value.trim();
    if (key) {
      localStorage.setItem(API_KEY_STORAGE, key);
      modal.style.display = 'none';
    }
  });
  input.addEventListener('keydown', e => { if (e.key === 'Enter') saveBtn.click(); });

  if (!getApiKey()) modal.style.display = 'flex';
})();

// ---- Pre-fill from URL params ----
(function readUrl() {
  const p = new URLSearchParams(window.location.search);
  const cid = p.get('chain_id');
  if (cid) inpChain.value = cid;
})();

// ---- Validate and parse policy override ----
function parsePolicyOverride() {
  const raw = inpPolicy.value.trim();
  if (!raw) return { ok: true, value: null };
  try {
    const parsed = JSON.parse(raw);
    if (typeof parsed !== 'object' || Array.isArray(parsed)) {
      return { ok: false, msg: 'Policy override must be a JSON object.' };
    }
    policyError.style.display = 'none';
    return { ok: true, value: parsed };
  } catch (_) {
    return { ok: false, msg: 'Invalid JSON in override policy.' };
  }
}

// ---- Render diff table ----
function renderDiffs(diffs) {
  if (!diffs.length) {
    diffTbody.innerHTML = '<tr><td colspan="4" class="empty-state">No events found in the given time window.</td></tr>';
    return;
  }
  diffTbody.innerHTML = diffs.map(d => {
    const rowCls = d.changed ? 'row-changed' : '';
    const changedCell = d.changed
      ? '<span class="badge-changed">CHANGED</span>'
      : '<span style="color:var(--text-muted);font-size:12px;">--</span>';
    return `<tr class="${rowCls}">
      <td class="td-step">${escHtml(d.step_id)}</td>
      <td class="td-decision">${decisionBadge(d.original_decision)}</td>
      <td class="td-decision">${decisionBadge(d.replayed_decision)}</td>
      <td>${changedCell}</td>
    </tr>`;
  }).join('');
}

// ---- Render stats pills ----
function renderStats(result) {
  const evCount = Number(result.event_count) || 0;
  const chCount = Number(result.changed_count) || 0;
  const unchanged = evCount - chCount;
  statsRow.innerHTML = `
    <div class="stat-pill"><strong>${escHtml(String(evCount))}</strong>Events</div>
    <div class="stat-pill"><strong>${escHtml(String(chCount))}</strong>Changed</div>
    <div class="stat-pill"><strong>${escHtml(String(unchanged))}</strong>Unchanged</div>
  `;
}

// ---- Show result ----
function showResult(result) {
  resultEmpty.style.display = 'none';
  resultContent.style.display = 'block';

  resultChain.textContent = result.chain_id;

  const changedNum = Number(result.changed_count) || 0;
  if (changedNum > 0) {
    resultBadge.innerHTML = `<span class="result-changed">${escHtml(String(changedNum))} decision(s) changed</span>`;
  } else {
    resultBadge.innerHTML = `<span class="result-changed none">No changes</span>`;
  }

  renderStats(result);
  renderDiffs(result.diffs || []);
  statusMsg.textContent = `Replayed ${result.event_count} event(s)`;
}

// ---- Run replay ----
async function runReplay() {
  // Validate chain_id
  const chainId = inpChain.value.trim();
  if (!chainId) {
    showToast('Chain ID is required.', 'error');
    inpChain.focus();
    return;
  }

  // Validate timestamps
  const fromVal = inpFrom.value;
  const toVal   = inpTo.value;
  if (!fromVal || !toVal) {
    showToast('From and To timestamps are required.', 'error');
    return;
  }
  const fromTs = Math.floor(new Date(fromVal).getTime() / 1000);
  const toTs   = Math.floor(new Date(toVal).getTime() / 1000);
  if (isNaN(fromTs) || isNaN(toTs)) {
    showToast('Invalid timestamp values.', 'error');
    return;
  }
  if (fromTs >= toTs) {
    showToast('From timestamp must be before To timestamp.', 'error');
    return;
  }

  // Validate policy override JSON
  const policyResult = parsePolicyOverride();
  if (!policyResult.ok) {
    policyError.textContent = policyResult.msg;
    policyError.style.display = 'block';
    return;
  }
  policyError.style.display = 'none';

  // Build request body
  const body = {
    chain_id: chainId,
    from_timestamp: fromTs,
    to_timestamp: toTs,
  };
  if (policyResult.value !== null) {
    body.override_policy = policyResult.value;
  }

  btnRun.disabled = true;
  btnRun.textContent = 'Running...';
  statusMsg.textContent = 'Submitting replay...';

  try {
    const resp = await fetch('/replay', {
      method: 'POST',
      headers: apiHeaders(),
      body: JSON.stringify(body),
    });

    if (resp.status === 401) {
      document.getElementById('apikey-modal').style.display = 'flex';
      statusMsg.textContent = '';
      return;
    }
    if (!resp.ok) {
      const data = await resp.json().catch(() => ({}));
      throw new Error(data.detail || `HTTP ${resp.status}`);
    }

    const result = await resp.json();
    showResult(result);
    const cc = Number(result.changed_count) || 0;
    showToast(`Replay complete: ${cc} change(s)`, cc > 0 ? 'warning' : 'success');
  } catch (err) {
    statusMsg.textContent = `Error: ${err.message}`;
    showToast(err.message, 'error');
  } finally {
    btnRun.disabled = false;
    btnRun.textContent = 'Run Replay';
  }
}

btnRun.addEventListener('click', runReplay);

// Allow Enter in chain_id input to submit
inpChain.addEventListener('keydown', e => { if (e.key === 'Enter') runReplay(); });
