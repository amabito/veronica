// tenants.js -- Veronica Tenant Management page logic
// API: GET /tenants, POST /tenants, PUT /tenants/{id}, DELETE /tenants/{id},
//      GET /tenants/{id}/effective-policy

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

function parseJsonField(raw, fieldName) {
  const trimmed = raw.trim();
  if (!trimmed) return { ok: true, value: {} };
  try {
    const parsed = JSON.parse(trimmed);
    if (typeof parsed !== 'object' || Array.isArray(parsed)) {
      return { ok: false, msg: `${fieldName} must be a JSON object.` };
    }
    return { ok: true, value: parsed };
  } catch (_) {
    return { ok: false, msg: `Invalid JSON in ${fieldName}.` };
  }
}

// ---- App state ----
const appState = {
  tenants: [],       // flat list from API
  selectedId: null,
  editMode: false,
};

// ---- DOM refs ----
const statusMsg       = document.getElementById('status-msg');
const tenantCount     = document.getElementById('tenant-count');
const tenantTree      = document.getElementById('tenant-tree');
const btnNewTenant    = document.getElementById('btn-new-tenant');
const newTenantPanel  = document.getElementById('new-tenant-panel');
const btnTenantSubmit = document.getElementById('btn-tenant-submit');
const btnTenantCancel = document.getElementById('btn-tenant-cancel');
const ntId            = document.getElementById('nt-id');
const ntName          = document.getElementById('nt-name');
const ntParent        = document.getElementById('nt-parent');
const ntOverrides     = document.getElementById('nt-overrides');
const ntOverridesError = document.getElementById('nt-overrides-error');
const detailEmpty     = document.getElementById('detail-empty');
const detailContent   = document.getElementById('detail-content');
const detailTenantId  = document.getElementById('detail-tenant-id');
const detailTenantName = document.getElementById('detail-tenant-name');
const detailParent    = document.getElementById('detail-parent');
const detailOverridesCount = document.getElementById('detail-overrides-count');
const detailOverridesJson  = document.getElementById('detail-overrides-json');
const editingIndicator = document.getElementById('editing-indicator');
const viewMode        = document.getElementById('view-mode');
const editNameGroup   = document.getElementById('edit-name-group');
const editName        = document.getElementById('edit-name');
const viewOverrides   = document.getElementById('view-overrides');
const editOverridesGroup = document.getElementById('edit-overrides-group');
const editOverrides   = document.getElementById('edit-overrides');
const editOverridesError = document.getElementById('edit-overrides-error');
const viewActions     = document.getElementById('view-actions');
const editActions     = document.getElementById('edit-actions');
const btnEditTenant   = document.getElementById('btn-edit-tenant');
const btnViewPolicy   = document.getElementById('btn-view-policy');
const btnDeleteTenant = document.getElementById('btn-delete-tenant');
const btnSaveEdit     = document.getElementById('btn-save-edit');
const btnCancelEdit   = document.getElementById('btn-cancel-edit');

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

// ---- Effective policy modal ----
document.getElementById('policy-modal-close').addEventListener('click', () => {
  document.getElementById('policy-modal').style.display = 'none';
});
document.getElementById('policy-modal').addEventListener('click', (e) => {
  if (e.target === document.getElementById('policy-modal')) {
    document.getElementById('policy-modal').style.display = 'none';
  }
});

// ---- Confirm delete modal ----
let _pendingDelete = null;

function openConfirm(message, onConfirm) {
  _pendingDelete = onConfirm;
  document.getElementById('confirm-body').textContent = message;
  document.getElementById('confirm-modal').style.display = 'flex';
}

document.getElementById('confirm-cancel').addEventListener('click', () => {
  document.getElementById('confirm-modal').style.display = 'none';
  _pendingDelete = null;
});

document.getElementById('confirm-ok').addEventListener('click', () => {
  document.getElementById('confirm-modal').style.display = 'none';
  if (_pendingDelete) _pendingDelete();
  _pendingDelete = null;
});

// ---- New tenant form ----
btnNewTenant.addEventListener('click', () => {
  const visible = newTenantPanel.style.display !== 'none';
  newTenantPanel.style.display = visible ? 'none' : 'block';
  if (!visible) {
    populateParentDropdown(ntParent, null);
    ntId.focus();
  }
});

btnTenantCancel.addEventListener('click', () => {
  newTenantPanel.style.display = 'none';
});

btnTenantSubmit.addEventListener('click', async () => {
  const id   = ntId.value.trim();
  const name = ntName.value.trim();

  if (!id)   { showToast('Tenant ID is required.', 'error'); ntId.focus(); return; }
  if (!name) { showToast('Display name is required.', 'error'); ntName.focus(); return; }

  const overridesResult = parseJsonField(ntOverrides.value, 'Policy Overrides');
  if (!overridesResult.ok) {
    ntOverridesError.textContent = overridesResult.msg;
    ntOverridesError.style.display = 'block';
    return;
  }
  ntOverridesError.style.display = 'none';

  const payload = {
    id,
    name,
    policy_overrides: overridesResult.value,
  };
  const parentVal = ntParent.value;
  if (parentVal) payload.parent_id = parentVal;

  btnTenantSubmit.disabled = true;
  btnTenantSubmit.textContent = 'Creating...';

  try {
    const resp = await fetch('/tenants', {
      method: 'POST',
      headers: apiHeaders(true),
      body: JSON.stringify(payload),
    });
    if (!resp.ok) {
      const data = await resp.json().catch(() => ({}));
      throw new Error(data.detail || `HTTP ${resp.status}`);
    }
    const created = await resp.json();
    showToast(`Tenant "${created.name}" created.`);
    newTenantPanel.style.display = 'none';
    ntId.value = '';
    ntName.value = '';
    ntOverrides.value = '';
    await fetchTenants();
    selectTenant(created.id);
  } catch (err) {
    showToast(err.message, 'error');
  } finally {
    btnTenantSubmit.disabled = false;
    btnTenantSubmit.textContent = 'Create Tenant';
  }
});

// ---- Fetch all tenants ----
async function fetchTenants() {
  statusMsg.textContent = 'Loading...';

  try {
    const resp = await fetch('/tenants', { headers: apiHeaders() });
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
    // Support both { items } and plain array
    appState.tenants = Array.isArray(data) ? data : (data.items || []);
    tenantCount.textContent = `${appState.tenants.length} tenant(s)`;
    renderTree();
    statusMsg.textContent = `Updated ${new Date().toLocaleTimeString()}`;
  } catch (err) {
    statusMsg.textContent = `Error: ${err.message}`;
    tenantTree.innerHTML = `<div class="empty-state">Failed to load: ${escHtml(err.message)}</div>`;
    showToast(err.message, 'error');
  }
}

// ---- Build tree structure (children under parent) ----
function buildTree(tenants) {
  const byId = {};
  tenants.forEach(t => { byId[t.id] = { ...t, children: [] }; });

  const roots = [];
  tenants.forEach(t => {
    if (t.parent_id && byId[t.parent_id]) {
      byId[t.parent_id].children.push(byId[t.id]);
    } else {
      roots.push(byId[t.id]);
    }
  });

  // Sort by name within each level
  function sortLevel(nodes) {
    nodes.sort((a, b) => (a.name || a.id).localeCompare(b.name || b.id));
    nodes.forEach(n => sortLevel(n.children));
  }
  sortLevel(roots);
  return roots;
}

// ---- Render tree ----
function renderTree() {
  const roots = buildTree(appState.tenants);
  if (!roots.length) {
    tenantTree.innerHTML = '<div class="empty-state">No tenants found.</div>';
    return;
  }

  const fragments = [];
  function walk(nodes, depth) {
    nodes.forEach(node => {
      const overrideCount = node.policy_overrides
        ? Object.keys(node.policy_overrides).length : 0;
      const sel = node.id === appState.selectedId ? 'selected' : '';
      const indent = '  '.repeat(depth);  // visual indent via padding
      const indentPx = depth * 20;

      fragments.push(`<div class="tree-item" data-id="${escHtml(node.id)}">
        <div class="tree-row ${sel}" data-id="${escHtml(node.id)}" style="padding-left:${16 + indentPx}px;">
          <span class="tree-name" title="${escHtml(node.name || node.id)}">${escHtml(node.name || node.id)}</span>
          <span class="tree-id">${escHtml(node.id)}</span>
          ${overrideCount > 0 ? `<span class="tree-overrides-count">${overrideCount} override${overrideCount !== 1 ? 's' : ''}</span>` : ''}
          <span class="tree-actions">
            <button class="btn-icon" data-action="edit" data-id="${escHtml(node.id)}" title="Edit tenant">Edit</button>
            <button class="btn-icon" data-action="policy" data-id="${escHtml(node.id)}" title="View effective policy">Policy</button>
            <button class="btn-icon danger" data-action="delete" data-id="${escHtml(node.id)}" title="Delete tenant">Delete</button>
          </span>
        </div>
      </div>`);

      if (node.children.length) walk(node.children, depth + 1);
    });
  }
  walk(roots, 0);

  tenantTree.innerHTML = fragments.join('');

  // Row click (not on action buttons) -> select
  tenantTree.querySelectorAll('.tree-row').forEach(row => {
    row.addEventListener('click', (e) => {
      if (e.target.closest('.btn-icon')) return;
      selectTenant(row.dataset.id);
    });
  });

  // Action buttons
  tenantTree.querySelectorAll('.btn-icon').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const id = btn.dataset.id;
      const action = btn.dataset.action;
      if (action === 'edit') {
        selectTenant(id);
        enterEditMode();
      } else if (action === 'policy') {
        selectTenant(id);
        showEffectivePolicy(id);
      } else if (action === 'delete') {
        confirmDelete(id);
      }
    });
  });
}

// ---- Select tenant (show detail panel) ----
function selectTenant(id) {
  appState.selectedId = id;
  exitEditMode(false); // reset edit state on selection change

  // Update selected highlight
  tenantTree.querySelectorAll('.tree-row').forEach(row => {
    row.classList.toggle('selected', row.dataset.id === id);
  });

  const t = appState.tenants.find(x => x.id === id);
  if (!t) return;

  detailEmpty.style.display = 'none';
  detailContent.style.display = 'block';

  detailTenantId.textContent   = t.id;
  detailTenantName.textContent = t.name || '--';
  detailParent.textContent     = t.parent_id || '(root)';

  const overrides = t.policy_overrides || {};
  const count = Object.keys(overrides).length;
  detailOverridesCount.textContent = count;
  detailOverridesJson.textContent = count > 0
    ? JSON.stringify(overrides, null, 2)
    : '(none)';
}

// ---- Edit mode ----
function enterEditMode() {
  if (!appState.selectedId) return;
  const t = appState.tenants.find(x => x.id === appState.selectedId);
  if (!t) return;

  appState.editMode = true;
  editingIndicator.style.display = 'inline';

  viewMode.style.display = 'none';
  editNameGroup.style.display = 'block';
  viewOverrides.style.display = 'none';
  editOverridesGroup.style.display = 'block';
  viewActions.style.display = 'none';
  editActions.style.display = 'flex';

  editName.value = t.name || '';
  editOverrides.value = t.policy_overrides && Object.keys(t.policy_overrides).length > 0
    ? JSON.stringify(t.policy_overrides, null, 2)
    : '';
  editOverridesError.style.display = 'none';
  editName.focus();
}

function exitEditMode(render = true) {
  appState.editMode = false;
  editingIndicator.style.display = 'none';

  viewMode.style.display = '';
  editNameGroup.style.display = 'none';
  viewOverrides.style.display = '';
  editOverridesGroup.style.display = 'none';
  viewActions.style.display = 'flex';
  editActions.style.display = 'none';
  editOverridesError.style.display = 'none';
}

btnEditTenant.addEventListener('click', enterEditMode);

btnCancelEdit.addEventListener('click', () => {
  exitEditMode();
  if (appState.selectedId) selectTenant(appState.selectedId);
});

btnSaveEdit.addEventListener('click', async () => {
  if (!appState.selectedId) return;

  const name = editName.value.trim();
  if (!name) { showToast('Name is required.', 'error'); editName.focus(); return; }

  const overridesResult = parseJsonField(editOverrides.value, 'Policy Overrides');
  if (!overridesResult.ok) {
    editOverridesError.textContent = overridesResult.msg;
    editOverridesError.style.display = 'block';
    return;
  }
  editOverridesError.style.display = 'none';

  btnSaveEdit.disabled = true;
  btnSaveEdit.textContent = 'Saving...';

  try {
    const resp = await fetch(`/tenants/${encodeURIComponent(appState.selectedId)}`, {
      method: 'PUT',
      headers: apiHeaders(true),
      body: JSON.stringify({ name, policy_overrides: overridesResult.value }),
    });
    if (!resp.ok) {
      const data = await resp.json().catch(() => ({}));
      throw new Error(data.detail || `HTTP ${resp.status}`);
    }
    const updated = await resp.json();
    // Update local state
    const idx = appState.tenants.findIndex(x => x.id === appState.selectedId);
    if (idx !== -1) appState.tenants[idx] = updated;
    exitEditMode();
    renderTree();
    selectTenant(updated.id);
    showToast('Tenant updated.');
  } catch (err) {
    showToast(err.message, 'error');
  } finally {
    btnSaveEdit.disabled = false;
    btnSaveEdit.textContent = 'Save';
  }
});

// ---- View effective policy ----
btnViewPolicy.addEventListener('click', () => {
  if (appState.selectedId) showEffectivePolicy(appState.selectedId);
});

async function showEffectivePolicy(tenantId) {
  const modal = document.getElementById('policy-modal');
  const box   = document.getElementById('effective-policy-box');
  const title = document.getElementById('policy-modal-title');
  const t = appState.tenants.find(x => x.id === tenantId);

  title.textContent = `Effective Policy -- ${t ? t.name || tenantId : tenantId}`;
  box.textContent = 'Loading...';
  modal.style.display = 'flex';

  try {
    const resp = await fetch(`/tenants/${encodeURIComponent(tenantId)}/effective-policy`, {
      headers: apiHeaders(),
    });
    if (!resp.ok) {
      const data = await resp.json().catch(() => ({}));
      throw new Error(data.detail || `HTTP ${resp.status}`);
    }
    const policy = await resp.json();
    box.textContent = JSON.stringify(policy, null, 2);
  } catch (err) {
    box.textContent = `Error: ${err.message}`;
    showToast(err.message, 'error');
  }
}

// ---- Delete tenant ----
function confirmDelete(tenantId) {
  const t = appState.tenants.find(x => x.id === tenantId);
  const label = t ? `"${t.name}" (${tenantId})` : tenantId;
  openConfirm(
    `Delete tenant ${label}? This action cannot be undone.`,
    () => deleteTenant(tenantId)
  );
}

async function deleteTenant(tenantId) {
  try {
    const resp = await fetch(`/tenants/${encodeURIComponent(tenantId)}`, {
      method: 'DELETE',
      headers: apiHeaders(),
    });
    if (!resp.ok) {
      const data = await resp.json().catch(() => ({}));
      throw new Error(data.detail || `HTTP ${resp.status}`);
    }
    showToast('Tenant deleted.');
    if (appState.selectedId === tenantId) {
      appState.selectedId = null;
      detailEmpty.style.display = '';
      detailContent.style.display = 'none';
    }
    await fetchTenants();
  } catch (err) {
    showToast(err.message, 'error');
  }
}

// ---- Populate parent dropdown ----
function populateParentDropdown(selectEl, excludeId) {
  const current = selectEl.value;
  selectEl.innerHTML = '<option value="">(none -- root tenant)</option>';
  appState.tenants
    .filter(t => t.id !== excludeId)
    .sort((a, b) => (a.name || a.id).localeCompare(b.name || b.id))
    .forEach(t => {
      const opt = document.createElement('option');
      opt.value = t.id;
      opt.textContent = `${t.name || t.id} (${t.id})`;
      selectEl.appendChild(opt);
    });
  if (current && current !== excludeId) selectEl.value = current;
}

// ---- Init ----
function init() {
  if (!getApiKey()) return;
  fetchTenants();
}

document.addEventListener('DOMContentLoaded', init);
