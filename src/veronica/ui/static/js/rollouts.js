// rollouts.js -- Veronica Rollout Pipeline page logic
// API: GET /rollouts, POST /rollouts, POST /rollouts/{id}/{action}

'use strict';

const API_KEY_STORAGE = 'veronica_api_key';

function getApiKey() {
  return localStorage.getItem(API_KEY_STORAGE) || '';
}

function apiHeaders(json = false) {
  const h = { 'X-Veronica-Key': getApiKey() };
  if (json) h['Content-Type'] = 'application/json';
  return h;
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

function stateBadge(state) {
  const s = (state || 'draft').toLowerCase();
  const known = ['draft', 'simulated', 'approved', 'promoted', 'active', 'revoked'];
  const cls = known.includes(s) ? `badge-${s}` : 'badge-default';
  return `<span class="badge ${cls}">${escHtml(s)}</span>`;
}

function formatIso(iso) {
  if (!iso) return '--';
  return new Date(iso).toLocaleString();
}

function truncateId(id) {
  if (!id) return '--';
  return id.length > 14 ? id.slice(0, 14) + '...' : id;
}

// ---- Valid state transitions ----
// DRAFT: simulate, revoke
// SIMULATED: approve, revoke
// APPROVED: promote, revoke
// PROMOTED: activate, revoke
// ACTIVE: revoke
// REVOKED: (none)
const TRANSITIONS = {
  draft:     [
    { label: 'Simulate', action: 'simulate', cls: 'btn-simulate', needsActor: false },
    { label: 'Revoke',   action: 'revoke',   cls: 'btn-revoke',   needsActor: true  },
  ],
  simulated: [
    { label: 'Approve',  action: 'approve',  cls: 'btn-approve',  needsActor: true  },
    { label: 'Revoke',   action: 'revoke',   cls: 'btn-revoke',   needsActor: true  },
  ],
  approved:  [
    { label: 'Promote',  action: 'promote',  cls: 'btn-promote',  needsActor: true  },
    { label: 'Revoke',   action: 'revoke',   cls: 'btn-revoke',   needsActor: true  },
  ],
  promoted:  [
    { label: 'Activate', action: 'activate', cls: 'btn-activate', needsActor: true  },
    { label: 'Revoke',   action: 'revoke',   cls: 'btn-revoke',   needsActor: true  },
  ],
  active:    [
    { label: 'Revoke',   action: 'revoke',   cls: 'btn-revoke',   needsActor: true  },
  ],
  revoked:   [],
};

// ---- App state ----
const appState = {
  rollouts: [],
  total: 0,
  page: 1,
  perPage: 50,
  stateFilter: '',
  selectedId: null,
  loading: false,
  refreshTimer: null,
};

// ---- DOM refs ----
const tbody         = document.getElementById('rollouts-tbody');
const rolloutCount  = document.getElementById('rollout-count');
const statusMsg     = document.getElementById('status-msg');
const filterState   = document.getElementById('filter-state');
const btnNewRollout = document.getElementById('btn-new-rollout');
const newRolloutPanel = document.getElementById('new-rollout-panel');
const btnCreateSubmit = document.getElementById('btn-create-submit');
const btnCreateCancel = document.getElementById('btn-create-cancel');
const nrChainId     = document.getElementById('nr-chain-id');
const nrCeilingUsd  = document.getElementById('nr-ceiling-usd');
const nrOnExceed    = document.getElementById('nr-on-exceed');
const nrCreatedBy   = document.getElementById('nr-created-by');
const detailEmpty   = document.getElementById('detail-empty');
const detailContent = document.getElementById('detail-content');
const detailId      = document.getElementById('detail-id');
const detailChain   = document.getElementById('detail-chain');
const detailCeiling = document.getElementById('detail-ceiling');
const detailOnExceed= document.getElementById('detail-on-exceed');
const detailBy      = document.getElementById('detail-by');
const detailCreated = document.getElementById('detail-created');
const detailStateBadge = document.getElementById('detail-state-badge');
const simSection    = document.getElementById('sim-section');
const detailSim     = document.getElementById('detail-sim');
const detailHistory = document.getElementById('detail-history');
const actionBar     = document.getElementById('action-bar');

// ---- API Key modal ----
(function initApiKey() {
  const modal   = document.getElementById('apikey-modal');
  const input   = document.getElementById('apikey-input');
  const saveBtn = document.getElementById('apikey-save');

  saveBtn.addEventListener('click', () => {
    const key = input.value.trim();
    if (!key) return;
    localStorage.setItem(API_KEY_STORAGE, key);
    modal.style.display = 'none';
    init();
  });
  input.addEventListener('keydown', e => { if (e.key === 'Enter') saveBtn.click(); });

  if (!getApiKey()) modal.style.display = 'flex';
})();

// ---- Actor modal ----
let _pendingAction = null;

function openActorModal(title, subtitle, onConfirm) {
  _pendingAction = onConfirm;
  document.getElementById('actor-modal-title').textContent = title;
  const subEl = document.getElementById('actor-modal-subtitle');
  if (subEl) subEl.textContent = subtitle || '';
  document.getElementById('actor-input').value = '';
  document.getElementById('actor-modal').style.display = 'flex';
  setTimeout(() => document.getElementById('actor-input').focus(), 60);
}

document.getElementById('actor-cancel').addEventListener('click', () => {
  document.getElementById('actor-modal').style.display = 'none';
  _pendingAction = null;
});

document.getElementById('actor-confirm').addEventListener('click', () => {
  const actor = document.getElementById('actor-input').value.trim();
  if (!actor) { showToast('Actor name is required.', 'error'); return; }
  document.getElementById('actor-modal').style.display = 'none';
  if (_pendingAction) _pendingAction(actor);
  _pendingAction = null;
});

document.getElementById('actor-input').addEventListener('keydown', e => {
  if (e.key === 'Enter') document.getElementById('actor-confirm').click();
});

// ---- New rollout form toggle ----
btnNewRollout.addEventListener('click', () => {
  const visible = newRolloutPanel.style.display !== 'none';
  newRolloutPanel.style.display = visible ? 'none' : 'block';
  if (!visible) nrChainId.focus();
});

btnCreateCancel.addEventListener('click', () => {
  newRolloutPanel.style.display = 'none';
});

// ---- Create rollout ----
btnCreateSubmit.addEventListener('click', async () => {
  const chainId    = nrChainId.value.trim();
  const ceilingRaw = nrCeilingUsd.value.trim();
  const onExceed   = nrOnExceed.value;
  const createdBy  = nrCreatedBy.value.trim();

  if (!chainId) { showToast('Chain ID is required.', 'error'); nrChainId.focus(); return; }
  const ceiling = parseFloat(ceilingRaw);
  if (isNaN(ceiling) || ceiling < 0) {
    showToast('Ceiling USD must be a non-negative number.', 'error');
    nrCeilingUsd.focus();
    return;
  }

  btnCreateSubmit.disabled = true;
  btnCreateSubmit.textContent = 'Creating...';

  const payload = {
    chain_id:    chainId,
    ceiling_usd: ceiling,
    on_exceed:   onExceed,
  };
  if (createdBy) payload.created_by = createdBy;

  try {
    const resp = await fetch('/rollouts', {
      method: 'POST',
      headers: apiHeaders(true),
      body: JSON.stringify(payload),
    });
    if (!resp.ok) {
      const data = await resp.json().catch(() => ({}));
      throw new Error(data.detail || `HTTP ${resp.status}`);
    }
    const created = await resp.json();
    showToast(`Rollout created.`);
    newRolloutPanel.style.display = 'none';
    nrChainId.value = '';
    nrCeilingUsd.value = '';
    nrCreatedBy.value = '';
    appState.page = 1;
    await fetchRollouts();
    selectRollout(created.id);
  } catch (err) {
    showToast(err.message, 'error');
  } finally {
    btnCreateSubmit.disabled = false;
    btnCreateSubmit.textContent = 'Create';
  }
});

// ---- Fetch rollouts ----
async function fetchRollouts() {
  if (appState.loading) return;
  appState.loading = true;
  statusMsg.textContent = 'Loading...';

  const params = new URLSearchParams();
  params.set('page', appState.page);
  params.set('per_page', appState.perPage);
  if (appState.stateFilter) params.set('state', appState.stateFilter);

  try {
    const resp = await fetch(`/rollouts?${params}`, { headers: apiHeaders() });
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
    // Support both paginated { items, total } and plain array responses
    if (Array.isArray(data)) {
      appState.rollouts = data;
      appState.total = data.length;
    } else {
      appState.rollouts = data.items || [];
      appState.total = data.total || appState.rollouts.length;
    }
    renderTable();
    rolloutCount.textContent = `${appState.total} rollout(s)`;
    statusMsg.textContent = `Updated ${new Date().toLocaleTimeString()}`;
  } catch (err) {
    statusMsg.textContent = `Error: ${err.message}`;
    tbody.innerHTML = `<tr><td colspan="7" class="empty-state">Failed to load: ${escHtml(err.message)}</td></tr>`;
    showToast(err.message, 'error');
  } finally {
    appState.loading = false;
  }
}

// ---- Render table ----
function renderTable() {
  if (!appState.rollouts.length) {
    tbody.innerHTML = '<tr><td colspan="7" class="empty-state">No rollouts found.</td></tr>';
    return;
  }

  tbody.innerHTML = appState.rollouts.map(r => {
    const sel = r.id === appState.selectedId ? 'selected' : '';
    const ceiling = r.ceiling_usd != null ? '$' + Number(r.ceiling_usd).toFixed(2) : '--';
    const transitions = TRANSITIONS[r.state] || [];

    const actionBtns = transitions.map(t =>
      `<button class="btn-action ${t.cls}" data-id="${escHtml(r.id)}" data-action="${escHtml(t.action)}" data-needs-actor="${t.needsActor}">${escHtml(t.label)}</button>`
    ).join('');

    return `<tr class="rollout-row ${sel}" data-id="${escHtml(r.id)}">
      <td class="td-id" title="${escHtml(r.id)}">${escHtml(truncateId(r.id))}</td>
      <td>${stateBadge(r.state)}</td>
      <td class="td-chain">${escHtml(r.chain_id || '--')}</td>
      <td class="td-cost">${escHtml(ceiling)}</td>
      <td class="td-ts">${escHtml(formatIso(r.created_at))}</td>
      <td class="td-by">${escHtml(r.created_by || '--')}</td>
      <td><div class="action-cell">${actionBtns || '<span style="color:var(--text-muted);font-size:11px;">--</span>'}</div></td>
    </tr>`;
  }).join('');

  // Row click -> select for detail panel
  tbody.querySelectorAll('.rollout-row').forEach(row => {
    row.addEventListener('click', (e) => {
      // Don't trigger row select when clicking action buttons
      if (e.target.closest('.btn-action')) return;
      selectRollout(row.dataset.id);
    });
  });

  // Action button clicks
  tbody.querySelectorAll('.btn-action').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const id = btn.dataset.id;
      const action = btn.dataset.action;
      const needsActor = btn.dataset.needsActor === 'true';
      handleTransition(id, action, needsActor);
    });
  });
}

// ---- Select rollout (populate detail panel) ----
function selectRollout(id) {
  appState.selectedId = id;
  // Re-render to update selected highlight
  tbody.querySelectorAll('.rollout-row').forEach(row => {
    row.classList.toggle('selected', row.dataset.id === id);
  });

  const r = appState.rollouts.find(x => x.id === id);
  if (!r) return;

  detailEmpty.style.display = 'none';
  detailContent.style.display = 'block';

  detailId.textContent      = r.id;
  detailChain.textContent   = r.chain_id || '--';
  detailCeiling.textContent = r.ceiling_usd != null ? '$' + Number(r.ceiling_usd).toFixed(4) : '--';
  detailOnExceed.textContent= r.on_exceed || '--';
  detailBy.textContent      = r.created_by || '--';
  detailCreated.textContent = formatIso(r.created_at);
  detailStateBadge.innerHTML = stateBadge(r.state);

  if (r.simulation_result) {
    simSection.style.display = 'block';
    detailSim.textContent = JSON.stringify(r.simulation_result, null, 2);
  } else {
    simSection.style.display = 'none';
  }

  renderHistory(r);
  renderDetailActions(r);
}

// ---- Render history timeline ----
function renderHistory(r) {
  const history = r.history || r.transitions || [];
  if (!history.length) {
    detailHistory.innerHTML = '<li style="color:var(--text-muted);">No transitions yet.</li>';
    return;
  }
  detailHistory.innerHTML = history.map(t => {
    const ts = t.timestamp || t.at ? `<span class="tl-ts">${escHtml(formatIso(t.timestamp || t.at))}</span>` : '';
    const fromTo = t.from_state
      ? `${stateBadge(t.from_state)} <span class="tl-arrow" style="color:var(--text-muted);">-></span> ${stateBadge(t.to_state)}`
      : stateBadge(t.state || t.to_state);
    const actor = t.actor ? `<span class="tl-actor">${escHtml(t.actor)}</span>` : '';
    return `<li>${ts}${fromTo}${actor}</li>`;
  }).join('');
}

// ---- Render action bar in detail panel ----
function renderDetailActions(r) {
  const transitions = TRANSITIONS[r.state] || [];
  if (!transitions.length) {
    actionBar.innerHTML = '<span style="color:var(--text-muted);font-size:12px;">No further actions (terminal state).</span>';
    return;
  }
  actionBar.innerHTML = transitions.map(t =>
    `<button class="btn btn-sm btn-action ${t.cls}" data-id="${escHtml(r.id)}" data-action="${escHtml(t.action)}" data-needs-actor="${t.needsActor}">${escHtml(t.label)}</button>`
  ).join('');

  actionBar.querySelectorAll('button[data-action]').forEach(btn => {
    btn.addEventListener('click', () => {
      handleTransition(btn.dataset.id, btn.dataset.action, btn.dataset.needsActor === 'true');
    });
  });
}

// ---- Handle transition (with optional actor prompt) ----
function handleTransition(id, action, needsActor) {
  if (needsActor) {
    const r = appState.rollouts.find(x => x.id === id);
    const idShort = id ? id.slice(0, 8) : id;
    openActorModal(
      `${action.charAt(0).toUpperCase() + action.slice(1)} Rollout`,
      `Rollout ${idShort}... -- ${r ? r.chain_id : ''}`,
      actor => doTransition(id, action, actor)
    );
  } else {
    doTransition(id, action, null);
  }
}

// ---- Perform transition API call ----
const VALID_ACTIONS = ['simulate', 'approve', 'promote', 'activate', 'revoke'];

async function doTransition(rolloutId, action, actor) {
  if (!VALID_ACTIONS.includes(action)) {
    showToast('Invalid action: ' + action, 'error');
    return;
  }
  statusMsg.textContent = `${action}...`;
  try {
    const payload = actor ? { actor } : {};
    const resp = await fetch(`/rollouts/${encodeURIComponent(rolloutId)}/${action}`, {
      method: 'POST',
      headers: apiHeaders(true),
      body: JSON.stringify(payload),
    });
    if (!resp.ok) {
      const data = await resp.json().catch(() => ({}));
      throw new Error(data.detail || `HTTP ${resp.status}`);
    }
    const updated = await resp.json();
    // Update local state without full refetch
    const idx = appState.rollouts.findIndex(x => x.id === rolloutId);
    if (idx !== -1) appState.rollouts[idx] = updated;
    renderTable();
    selectRollout(rolloutId);
    showToast(`Rollout ${action}d successfully.`);
    statusMsg.textContent = `Updated ${new Date().toLocaleTimeString()}`;
  } catch (err) {
    showToast(err.message, 'error');
    statusMsg.textContent = `Error: ${err.message}`;
  }
}

// ---- Filter change ----
filterState.addEventListener('change', () => {
  appState.stateFilter = filterState.value;
  appState.page = 1;
  fetchRollouts();
});

// ---- Auto-refresh every 10 seconds ----
function startAutoRefresh() {
  if (appState.refreshTimer) clearInterval(appState.refreshTimer);
  appState.refreshTimer = setInterval(fetchRollouts, 10000);
}

// ---- Init ----
function init() {
  if (!getApiKey()) return;
  fetchRollouts();
  startAutoRefresh();
}

document.addEventListener('DOMContentLoaded', init);

window.addEventListener('beforeunload', () => {
  if (appState.refreshTimer) clearInterval(appState.refreshTimer);
});
