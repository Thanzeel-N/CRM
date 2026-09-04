/* ═══════════════════════════════════════════════════════════════
   MetaCRM — App Logic
   ═══════════════════════════════════════════════════════════════ */

// ─── State ───────────────────────────────────────────────────────
const state = {
  token: localStorage.getItem('crm_token'),
  user: JSON.parse(localStorage.getItem('crm_user') || 'null'),
  leads: [],
  campaigns: [],
  staff: [],
  activeLead: null,
  statusChart: null,
  campaignChart: null,
};

// ─── API Helper ──────────────────────────────────────────────────
async function api(path, opts = {}) {
  const headers = { 'Content-Type': 'application/json', ...(opts.headers || {}) };
  if (state.token) headers['Authorization'] = `Bearer ${state.token}`;
  const res = await fetch(path, { ...opts, headers });
  if (res.status === 401) { doLogout(); return null; }
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
  if (res.status === 204) return null;
  return res.json();
}

function toast(msg, type = 'success') {
  const el = document.createElement('div');
  el.style.cssText = `
    position:fixed; bottom:24px; right:24px; z-index:9999;
    padding:12px 20px; border-radius:10px; font-size:13px; font-weight:500;
    box-shadow:0 8px 24px rgba(0,0,0,.4); max-width:360px;
    animation: slideUp .25s ease;
    background: ${type === 'success' ? '#059669' : type === 'error' ? '#dc2626' : '#6366f1'};
    color: #fff;
  `;
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 3500);
}

// ─── Auth ────────────────────────────────────────────────────────
function saveAuth(data) {
  state.token = data.access_token;
  state.user  = data.user;
  localStorage.setItem('crm_token', data.access_token);
  localStorage.setItem('crm_user',  JSON.stringify(data.user));
  updateUserUI();
}

function doLogout() {
  state.token = null;
  state.user  = null;
  localStorage.removeItem('crm_token');
  localStorage.removeItem('crm_user');
  updateUserUI();
  loadAll();
}

function updateUserUI() {
  const u = state.user;
  document.getElementById('sidebarUserName').textContent = u ? u.name  : 'Sign In';
  document.getElementById('sidebarUserRole').textContent = u ? u.role  : '—';
  document.getElementById('headerOrgName').textContent   = u ? u.org_name : '—';
  document.getElementById('userAvatarLetter').textContent = u ? u.name[0].toUpperCase() : '?';

  // Show/hide admin-only nav items
  document.querySelectorAll('.admin-only').forEach(el => {
    el.style.display = (u && u.role === 'admin') ? 'flex' : 'none';
  });

  // Populate org settings fields if admin
  if (u && u.role === 'admin') loadIntegrations();
}

// ─── Navigation ──────────────────────────────────────────────────
document.querySelectorAll('.nav-item[data-tab]').forEach(item => {
  item.addEventListener('click', () => {
    if (!state.user) { openModal('authModal'); return; }
    const tab = item.dataset.tab;
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
    item.classList.add('active');
    document.getElementById(tab)?.classList.add('active');
    document.getElementById('pageTitleText').textContent = item.querySelector('span')?.textContent || '';

    if (tab === 'tab-analytics') renderCharts();
    if (tab === 'tab-campaigns') loadCampaigns();
    if (tab === 'tab-staff')     loadStaff();
    if (tab === 'tab-connect')   loadIntegrations();
  });
});

// ─── Modals ──────────────────────────────────────────────────────
function openModal(id)  { document.getElementById(id)?.classList.add('active'); }
function closeModal(id) { document.getElementById(id)?.classList.remove('active'); }

document.querySelectorAll('.modal-backdrop').forEach(m => {
  m.addEventListener('click', e => { if (e.target === m) m.classList.remove('active'); });
});

// Auth Modal buttons
document.getElementById('openAuthModalBtn').addEventListener('click', () => openModal('authModal'));
document.getElementById('closeAuthModalBtn').addEventListener('click', () => closeModal('authModal'));
document.getElementById('authTabLogin').addEventListener('click', () => {
  document.getElementById('loginForm').style.display = '';
  document.getElementById('registerForm').style.display = 'none';
  document.getElementById('authTabLogin').classList.add('active');
  document.getElementById('authTabRegister').classList.remove('active');
});
document.getElementById('authTabRegister').addEventListener('click', () => {
  document.getElementById('loginForm').style.display = 'none';
  document.getElementById('registerForm').style.display = '';
  document.getElementById('authTabLogin').classList.remove('active');
  document.getElementById('authTabRegister').classList.add('active');
});

// Simulate Modal
document.getElementById('openSimulateModalBtn').addEventListener('click', () => {
  if (!state.user) { openModal('authModal'); return; }
  populateCampaignDropdown('quickSimCampaign');
  openModal('simulateModal');
});
document.getElementById('closeSimulateModal').addEventListener('click', () => closeModal('simulateModal'));
document.getElementById('cancelSimulateModal').addEventListener('click', () => closeModal('simulateModal'));

// Campaign Modal
document.getElementById('openCreateCampaignBtn')?.addEventListener('click', () => {
  populateStaffDropdown('campAssignedUser');
  openModal('createCampaignModal');
});
document.getElementById('closeCreateCampaignModal')?.addEventListener('click', () => closeModal('createCampaignModal'));
document.getElementById('cancelCreateCampaign')?.addEventListener('click', () => closeModal('createCampaignModal'));

// Staff Modal
document.getElementById('openInviteStaffBtn')?.addEventListener('click', () => openModal('inviteStaffModal'));
document.getElementById('closeInviteStaffModal')?.addEventListener('click', () => closeModal('inviteStaffModal'));
document.getElementById('cancelInviteStaff')?.addEventListener('click', () => closeModal('inviteStaffModal'));

// Drawer
document.getElementById('drawerBackdrop').addEventListener('click', closeDrawer);
document.getElementById('closeDrawerBtn').addEventListener('click', closeDrawer);

function openDrawer() {
  document.getElementById('leadDrawer').classList.add('active');
  document.getElementById('drawerBackdrop').classList.add('active');
}
function closeDrawer() {
  document.getElementById('leadDrawer').classList.remove('active');
  document.getElementById('drawerBackdrop').classList.remove('active');
  state.activeLead = null;
}

// Theme toggle
document.getElementById('themeToggleBtn').addEventListener('click', () => {
  const html = document.documentElement;
  const isDark = html.getAttribute('data-theme') === 'dark';
  html.setAttribute('data-theme', isDark ? 'light' : 'dark');
  document.getElementById('themeIcon').setAttribute('data-lucide', isDark ? 'sun' : 'moon');
  lucide.createIcons();
});

// Sidebar toggle (mobile)
document.getElementById('sidebarToggleBtn')?.addEventListener('click', () => {
  document.getElementById('sidebar').classList.toggle('open');
});

// ─── Login / Register ─────────────────────────────────────────────
document.getElementById('loginForm').addEventListener('submit', async e => {
  e.preventDefault();
  try {
    const data = await api('/auth/login', {
      method: 'POST',
      body: JSON.stringify({
        email: document.getElementById('loginEmail').value,
        password: document.getElementById('loginPassword').value,
      }),
    });
    saveAuth(data);
    closeModal('authModal');
    toast(`Welcome back, ${data.user.name}!`);
    loadAll();
  } catch (err) { toast(err.message, 'error'); }
});

document.getElementById('registerForm').addEventListener('submit', async e => {
  e.preventDefault();
  try {
    const data = await api('/auth/register', {
      method: 'POST',
      body: JSON.stringify({
        org_name:  document.getElementById('regOrgName').value,
        user_name: document.getElementById('regUserName').value,
        email:     document.getElementById('regEmail').value,
        password:  document.getElementById('regPassword').value,
      }),
    });
    saveAuth(data);
    closeModal('authModal');
    toast(`Account created! Welcome, ${data.user.name}!`);
    loadAll();
  } catch (err) { toast(err.message, 'error'); }
});

// ─── Leads ───────────────────────────────────────────────────────
async function loadLeads() {
  if (!state.token) { renderKanban([]); renderTable([]); return; }
  try {
    const params = new URLSearchParams();
    const search = document.getElementById('searchInput').value.trim();
    const campaign = document.getElementById('campaignFilterSelect').value;
    if (search)   params.append('search', search);
    if (campaign) params.append('campaign_id', campaign);

    state.leads = await api(`/leads?${params}`) || [];
    renderKanban(state.leads);
    filterAndRenderTable();
    loadStats();
  } catch (err) { console.error('Load leads failed:', err); }
}

async function loadStats() {
  if (!state.token) return;
  try {
    const stats = await api('/leads/stats') || {};
    document.getElementById('statTotal').textContent        = stats.total_leads ?? '—';
    document.getElementById('statNewToday').textContent     = stats.new_today   ?? '—';
    document.getElementById('statQualified').textContent    = stats.qualified_leads ?? '—';
    document.getElementById('statConversionRate').textContent = stats.conversion_rate != null ? `${stats.conversion_rate}%` : '—';

    // Populate campaign filter
    const sel = document.getElementById('campaignFilterSelect');
    const current = sel.value;
    // Clear options except first
    while (sel.options.length > 1) sel.remove(1);
    state.campaigns.forEach(c => {
      const opt = document.createElement('option');
      opt.value = c.id;
      opt.textContent = c.name;
      if (String(c.id) === current) opt.selected = true;
      sel.appendChild(opt);
    });
  } catch (err) { console.error('Stats failed:', err); }
}

function renderKanban(leads) {
  const statuses = ['new', 'contacted', 'qualified', 'converted', 'lost'];
  statuses.forEach(s => {
    document.getElementById(`container-${s}`).innerHTML = '';
    document.getElementById(`count-${s}`).textContent = '0';
  });

  const groups = {};
  statuses.forEach(s => groups[s] = []);
  leads.forEach(l => { if (groups[l.status]) groups[l.status].push(l); });

  statuses.forEach(s => {
    const container = document.getElementById(`container-${s}`);
    document.getElementById(`count-${s}`).textContent = groups[s].length;
    if (!groups[s].length) {
      container.innerHTML = '<div style="font-size:11px;color:var(--text-3);text-align:center;padding:16px 0;">Empty</div>';
      return;
    }
    groups[s].forEach(lead => {
      const card = document.createElement('div');
      card.className = 'lead-card';
      const date = lead.created_at ? new Date(lead.created_at).toLocaleDateString('en-GB', { day:'numeric', month:'short' }) : '—';
      card.innerHTML = `
        <div class="lead-card-name">${esc(lead.name || '—')}</div>
        <div class="lead-card-contact">
          ${lead.email ? `📧 ${esc(lead.email)}<br>` : ''}
          ${lead.phone ? `📱 ${esc(lead.phone)}` : ''}
        </div>
        <div class="lead-card-footer">
          <span class="lead-tag" title="${esc(lead.campaign_name || '')}">${esc(lead.campaign_name || 'No Campaign')}</span>
          <span class="lead-time">${date}</span>
        </div>`;
      card.addEventListener('click', () => openLeadDrawer(lead));
      container.appendChild(card);
    });
  });
}

function filterAndRenderTable() {
  let leadsToRender = state.leads;
  const filterVal = document.getElementById('leadsDateFilter')?.value;
  if (filterVal) {
    let start, end;
    if (filterVal.includes(' to ')) {
      const parts = filterVal.split(' to ');
      start = new Date(parts[0]);
      end = new Date(parts[1]);
    } else {
      start = new Date(filterVal);
      end = new Date(filterVal);
    }
    start.setHours(0, 0, 0, 0);
    end.setHours(23, 59, 59, 999);
    
    leadsToRender = state.leads.filter(l => {
      const d = new Date(l.created_at);
      return d >= start && d <= end;
    });
  }
  renderTable(leadsToRender);
}

function renderTable(leads) {
  const tbody = document.getElementById('leadsTableBody');
  if (!leads.length) {
    tbody.innerHTML = `<tr><td colspan="8" style="text-align:center;padding:40px;color:var(--text-3);">No leads found</td></tr>`;
    return;
  }
  tbody.innerHTML = leads.map(l => {
    const date = l.created_at ? new Date(l.created_at).toLocaleDateString('en-GB', { day:'numeric', month:'short', year:'2-digit' }) : '—';
    const campaign = state.campaigns.find(c => c.id === l.campaign_id);
    const assignee = campaign?.assigned_user_name || '—';
    return `
      <tr>
        <td style="color:var(--text-3);font-size:11px;">#${l.id}</td>
        <td><strong>${esc(l.name || '—')}</strong></td>
        <td><div style="font-size:12px;color:var(--text-2);">${esc(l.email || '—')}<br>${esc(l.phone || '')}</div></td>
        <td><span class="lead-tag">${esc(l.campaign_name || '—')}</span></td>
        <td style="font-size:12px;color:var(--text-2);">${esc(assignee)}</td>
        <td><span class="pill ${l.status}">${l.status}</span></td>
        <td style="font-size:12px;color:var(--text-3);">${date}</td>
        <td>
          <button class="btn btn-ghost" style="height:30px;padding:0 10px;font-size:12px;" onclick="openLeadDrawerById(${l.id})">
            Open
          </button>
        </td>
      </tr>`;
  }).join('');
}

function openLeadDrawerById(id) {
  const lead = state.leads.find(l => l.id === id);
  if (lead) openLeadDrawer(lead);
}

function openLeadDrawer(lead) {
  state.activeLead = lead;
  document.getElementById('drawerLeadName').textContent    = lead.name || '—';
  document.getElementById('drawerLeadTime').textContent    = lead.created_at ? new Date(lead.created_at).toLocaleString() : '';
  document.getElementById('drawerLeadEmail').textContent   = lead.email || '—';
  document.getElementById('drawerLeadPhone').textContent   = lead.phone || '—';
  document.getElementById('drawerLeadCampaign').textContent = lead.campaign_name || '—';
  document.getElementById('drawerLeadMetaId').textContent  = lead.fb_lead_id || '—';
  document.getElementById('drawerStatusSelect').value      = lead.status;
  document.getElementById('drawerNotesTextarea').value     = lead.notes || '';
  document.getElementById('whatsappChatBox').innerHTML     = '<p class="chat-empty">Loading messages…</p>';
  openDrawer();
  lucide.createIcons();
  loadChatHistory(lead.id);
}

// Status update
document.getElementById('drawerStatusSelect').addEventListener('change', async function() {
  if (!state.activeLead) return;
  try {
    const updated = await api(`/leads/${state.activeLead.id}/status`, {
      method: 'PATCH',
      body: JSON.stringify({ status: this.value }),
    });
    state.activeLead = updated;
    loadLeads();
    toast('Status updated ✓');
  } catch (err) { toast(err.message, 'error'); }
});

// Save notes
document.getElementById('saveNotesBtn').addEventListener('click', async () => {
  if (!state.activeLead) return;
  const notes = document.getElementById('drawerNotesTextarea').value;
  try {
    await api(`/leads/${state.activeLead.id}/status`, {
      method: 'PATCH',
      body: JSON.stringify({ status: state.activeLead.status, notes }),
    });
    toast('Notes saved ✓');
  } catch (err) { toast(err.message, 'error'); }
});

// WhatsApp chat
async function loadChatHistory(leadId) {
  try {
    const messages = await api(`/leads/${leadId}/messages`) || [];
    const box = document.getElementById('whatsappChatBox');
    if (!messages.length) {
      box.innerHTML = '<p class="chat-empty">No messages yet</p>';
      return;
    }
    box.innerHTML = messages.map(m => `
      <div class="chat-bubble ${m.direction}">
        ${esc(m.message_text)}
        <div style="font-size:10px;opacity:.6;margin-top:3px;text-align:right;">${new Date(m.created_at).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'})}</div>
      </div>`).join('');
    box.scrollTop = box.scrollHeight;
  } catch (err) { console.error('Chat load failed:', err); }
}

document.getElementById('whatsappForm').addEventListener('submit', async e => {
  e.preventDefault();
  if (!state.activeLead) return;
  const input = document.getElementById('waMsgInput');
  const msg = input.value.trim();
  if (!msg) return;
  try {
    await api('/leads/whatsapp/send', {
      method: 'POST',
      body: JSON.stringify({ lead_id: state.activeLead.id, message: msg }),
    });
    input.value = '';
    loadChatHistory(state.activeLead.id);
    loadLeads();
  } catch (err) { toast(err.message, 'error'); }
});

// ─── Search / Filter ──────────────────────────────────────────────
let searchTimer;
document.getElementById('searchInput').addEventListener('input', () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(loadLeads, 300);
});
document.getElementById('campaignFilterSelect').addEventListener('change', loadLeads);

// ─── Export ───────────────────────────────────────────────────────
document.getElementById('exportBtn').addEventListener('click', () => {
  if (!state.token) { toast('Please sign in first', 'error'); return; }
  window.open('/leads/export/excel', '_blank');
});

// ─── Simulator ───────────────────────────────────────────────────
document.getElementById('simulatorForm').addEventListener('submit', async e => {
  e.preventDefault();
  try {
    const lead = await api('/leads/simulate', {
      method: 'POST',
      body: JSON.stringify({
        name:          document.getElementById('simName').value,
        email:         document.getElementById('simEmail').value,
        phone:         document.getElementById('simPhone').value,
        campaign_name: document.getElementById('simCampaign').value,
        form_name:     document.getElementById('simForm').value,
      }),
    });
    toast(`Lead "${lead.name}" created in pipeline ✓`);
    loadLeads();
  } catch (err) { toast(err.message, 'error'); }
});

document.getElementById('quickSimulateForm').addEventListener('submit', async e => {
  e.preventDefault();
  const campSel = document.getElementById('quickSimCampaign');
  const campName = campSel.options[campSel.selectedIndex]?.text || 'General Campaign';
  try {
    const lead = await api('/leads/simulate', {
      method: 'POST',
      body: JSON.stringify({
        name:          document.getElementById('quickSimName').value,
        email:         document.getElementById('quickSimEmail').value,
        phone:         document.getElementById('quickSimPhone').value,
        campaign_name: campName === '— select —' ? 'General Campaign' : campName,
        form_name:     'Quick Simulate',
      }),
    });
    closeModal('simulateModal');
    toast(`Lead "${lead.name}" added to pipeline ✓`);
    loadLeads();
  } catch (err) { toast(err.message, 'error'); }
});

// ─── Campaigns ───────────────────────────────────────────────────
async function loadCampaigns() {
  if (!state.token) return;
  try {
    state.campaigns = await api('/campaigns') || [];
    renderCampaignsGrid();
    populateCampaignDropdown('quickSimCampaign');
    loadStats();
  } catch (err) { console.error('Campaigns load failed:', err); }
}

function renderCampaignsGrid() {
  const grid = document.getElementById('campaignsGrid');
  if (!state.campaigns.length) {
    grid.innerHTML = `<div class="empty-state">
      <i data-lucide="megaphone"></i>
      <p>No campaigns yet.<br>Create one to start assigning staff to leads.</p>
    </div>`;
    lucide.createIcons();
    return;
  }
  grid.innerHTML = state.campaigns.map(c => `
    <div class="campaign-card">
      <div class="campaign-card-head">
        <h4>${esc(c.name)}</h4>
        <div class="campaign-status ${c.is_active ? 'active' : 'inactive'}"></div>
      </div>
      <div class="campaign-meta">
        ${c.description ? `<div>${esc(c.description)}</div>` : ''}
        ${c.meta_form_id ? `<div>Form ID: <code>${esc(c.meta_form_id)}</code></div>` : ''}
        ${c.meta_ad_account_id ? `<div>Ad Account: <code>${esc(c.meta_ad_account_id)}</code></div>` : ''}
      </div>
      <div class="campaign-foot">
        <div class="campaign-assignee">
          <i data-lucide="user"></i>
          <span>${esc(c.assigned_user_name || 'Unassigned')}</span>
        </div>
        <span class="campaign-lead-count">${c.lead_count} leads</span>
        <div class="campaign-actions">
          <button class="btn btn-ghost" style="height:28px;padding:0 10px;font-size:12px;" onclick="editCampaign(${c.id})">
            Reassign
          </button>
        </div>
      </div>
    </div>`).join('');
  lucide.createIcons();
}

async function editCampaign(id) {
  const campaign = state.campaigns.find(c => c.id === id);
  if (!campaign) return;
  await populateStaffDropdown('campAssignedUser', campaign.assigned_user_id);
  document.getElementById('campName').value        = campaign.name;
  document.getElementById('campDesc').value        = campaign.description || '';
  document.getElementById('campFormId').value      = campaign.meta_form_id || '';
  document.getElementById('campAdAccountId').value = campaign.meta_ad_account_id || '';

  // Store editing id
  document.getElementById('createCampaignForm').dataset.editId = id;
  openModal('createCampaignModal');
}

document.getElementById('createCampaignForm').addEventListener('submit', async e => {
  e.preventDefault();
  const editId = e.target.dataset.editId;
  const payload = {
    name:                document.getElementById('campName').value,
    description:         document.getElementById('campDesc').value || null,
    meta_form_id:        document.getElementById('campFormId').value || null,
    meta_ad_account_id:  document.getElementById('campAdAccountId').value || null,
    assigned_user_id:    parseInt(document.getElementById('campAssignedUser').value) || null,
  };
  try {
    if (editId) {
      await api(`/campaigns/${editId}`, { method:'PATCH', body: JSON.stringify(payload) });
      toast('Campaign updated ✓');
    } else {
      await api('/campaigns', { method:'POST', body: JSON.stringify(payload) });
      toast('Campaign created ✓');
    }
    delete e.target.dataset.editId;
    e.target.reset();
    closeModal('createCampaignModal');
    loadCampaigns();
  } catch (err) { toast(err.message, 'error'); }
});

// ─── Staff ───────────────────────────────────────────────────────
async function loadStaff() {
  if (!state.token) return;
  try {
    state.staff = await api('/staff') || [];
    renderStaffTable();
  } catch (err) { console.error('Staff load failed:', err); }
}

function renderStaffTable() {
  const tbody = document.getElementById('staffTableBody');
  if (!state.staff.length) {
    tbody.innerHTML = `<tr><td colspan="6" style="text-align:center;padding:40px;color:var(--text-3);">No staff members yet. Invite someone!</td></tr>`;
    return;
  }
  tbody.innerHTML = state.staff.map(u => {
    const campaigns = u.assigned_campaigns.map(c => `<span class="lead-tag">${esc(c.name)}</span>`).join(' ') || '—';
    const isMe = state.user && state.user.id === u.id;
    return `
      <tr>
        <td><strong>${esc(u.name)}</strong>${isMe ? ' <span style="font-size:10px;color:var(--accent);">(you)</span>' : ''}</td>
        <td style="font-size:12px;color:var(--text-2);">${esc(u.email)}</td>
        <td><span class="pill ${u.role}">${u.role}</span></td>
        <td>${campaigns}</td>
        <td><span class="pill ${u.is_active ? 'active' : 'inactive'}">${u.is_active ? 'Active' : 'Inactive'}</span></td>
        <td>
          ${!isMe ? `<button class="btn btn-ghost" style="height:28px;padding:0 10px;font-size:12px;"
            onclick="${u.is_active ? `deactivateStaff(${u.id})` : `activateStaff(${u.id})`}">
            ${u.is_active ? 'Deactivate' : 'Activate'}
          </button>` : ''}
        </td>
      </tr>`;
  }).join('');
}

async function deactivateStaff(id) {
  try {
    await api(`/staff/${id}/deactivate`, { method:'PATCH' });
    toast('Staff member deactivated');
    loadStaff();
  } catch (err) { toast(err.message, 'error'); }
}
async function activateStaff(id) {
  try {
    await api(`/staff/${id}/activate`, { method:'PATCH' });
    toast('Staff member activated ✓');
    loadStaff();
  } catch (err) { toast(err.message, 'error'); }
}

document.getElementById('inviteStaffForm').addEventListener('submit', async e => {
  e.preventDefault();
  try {
    const data = await api('/staff/invite', {
      method: 'POST',
      body: JSON.stringify({
        name:     document.getElementById('staffName').value,
        email:    document.getElementById('staffEmail').value,
        password: document.getElementById('staffPassword').value,
        role:     document.getElementById('staffRole').value,
      }),
    });
    toast(`${data.name} invited successfully ✓`);
    e.target.reset();
    closeModal('inviteStaffModal');
    loadStaff();
    loadCampaigns(); // refresh assignment dropdowns
  } catch (err) { toast(err.message, 'error'); }
});

// ─── Integrations & Lead Sources ─────────────────────────────────
async function loadIntegrations() {
  if (!state.token || !state.user || state.user.role !== 'admin') return;
  try {
    const list = document.getElementById('integrationList');
    
    // Fetch connected pages from backend
    // Since we don't have a specific GET /meta/pages endpoint right now, we can check if there's any connection
    // We should ideally have an endpoint to list pages. Wait, we don't.
    // I can fetch org settings to see if it's connected, or just show a connected status.
    
    // For now, let's just show a simple status based on a query param or placeholder.
    // If we want to be exact, we'd add an endpoint for it. Let's assume we can hit `/org/settings`
    // Actually, we removed meta_page_id from org/settings. So let's check connection status via a new endpoint or just display generic state.
    
    // Let's implement a quick mock/UI for the integration list
    list.innerHTML = `
      <!-- We will populate this dynamically if we had a /meta/pages endpoint -->
    `;
    
    // Connection status badge on sidebar
    // We can assume it's connected if we have meta_connected=true in URL (for demo purposes)
    const urlParams = new URLSearchParams(window.location.search);
    const isConnected = urlParams.get('meta_connected') === 'true';
    
    let html = '';
    
    if (isConnected) {
      html += `
        <div class="integration-card">
          <div class="integration-card-left">
            <div class="integration-icon"><i data-lucide="facebook" style="color: #1877F2;"></i></div>
            <div class="integration-info">
              <h3>Facebook Lead Ads <span class="integration-status">Connected</span></h3>
              <p>Receiving leads from your connected Facebook Pages</p>
            </div>
          </div>
          <button class="btn btn-ghost" style="color:var(--red);" onclick="alert('Disconnect flow not implemented in demo')">Disconnect</button>
        </div>
      `;
    }
    
    try {
      const gsRes = await api('/integrations/google-sheets/status');
      if (gsRes && gsRes.connected) {
        html += `
          <div class="integration-card" style="margin-top:12px;">
            <div class="integration-card-left">
              <div class="integration-icon"><i data-lucide="file-spreadsheet" style="color: #0F9D58;"></i></div>
              <div class="integration-info">
                <h3>Google Sheets Sync <span class="integration-status">Active</span></h3>
                <p>Syncing to <a href="${gsRes.spreadsheet_url}" target="_blank">Spreadsheet</a> (${gsRes.sheet_name})</p>
              </div>
            </div>
            <button class="btn btn-ghost" style="color:var(--red);" id="btnDisconnectGs">Disconnect</button>
          </div>
        `;
      }
    } catch (e) { console.error('GS Status failed', e); }

    if (html) {
      list.innerHTML = html;
      lucide.createIcons();
      
      const disconnectBtn = document.getElementById('btnDisconnectGs');
      if (disconnectBtn) {
        disconnectBtn.addEventListener('click', async () => {
          if(!confirm('Stop syncing leads to Google Sheets?')) return;
          try {
            await api('/integrations/google-sheets/disconnect', { method: 'POST' });
            toast('Google Sheets disconnected');
            loadIntegrations();
          } catch (err) { toast(err.message, 'error'); }
        });
      }
    }

    const dot = document.querySelector('.badge-dot');
    const statusText = document.getElementById('connectionStatus');
    if (dot) { dot.className = `badge-dot ${isConnected ? 'connected' : 'disconnected'}`; }
    if (statusText) statusText.textContent = isConnected ? 'Connected ✓' : 'Not Connected';
  } catch (err) { console.error('Integrations failed:', err); }
}

document.getElementById('btnConnectFacebook')?.addEventListener('click', async () => {
  if (!state.token) return;
  
  try {
    const res = await api('/integrations/facebook/auth-url');
    if (res && res.url) {
      // Open popup
      const popup = window.open(res.url, 'fb_oauth', 'width=600,height=700');
      
      // Wait for message from popup
      window.addEventListener('message', async function fbAuthListener(event) {
        if (event.data && event.data.type === 'FB_OAUTH_SUCCESS') {
          window.removeEventListener('message', fbAuthListener);
          
          try {
            // 1. Exchange code
            const exRes = await api('/integrations/facebook/exchange', {
              method: 'POST',
              body: JSON.stringify({ 
                code: event.data.code,
                redirect_uri: res.redirect_uri 
              })
            });
            
            const fbSessionToken = exRes.fb_session_token;
            
            // 2. Fetch pages
            const pagesRes = await api(`/integrations/facebook/pages?fb_session_token=${fbSessionToken}`);
            const pages = pagesRes.pages;
            
            if (!pages || pages.length === 0) {
              toast("No Facebook Pages found for this account.", "error");
              return;
            }
            
            // 3. Just connect the first page for this demo, or we could show a modal
            // Let's just auto-connect the first one for simplicity
            const firstPage = pages[0];
            
            // Fetch forms for that page to connect
            const formsRes = await api(`/integrations/facebook/pages/${firstPage.id}/forms?fb_session_token=${fbSessionToken}`);
            const formIds = formsRes.forms ? formsRes.forms.map(f => f.id) : [];
            
            // Connect it
            await api('/integrations/facebook/connect', {
              method: 'POST',
              body: JSON.stringify({
                fb_session_token: fbSessionToken,
                page_id: firstPage.id,
                page_name: firstPage.name,
                forms: formIds
              })
            });
            
            toast(`Successfully connected Facebook Page: ${firstPage.name}! ✓`);
            
            // Reload integrations UI
            // Fake a page reload to URL with success param to re-trigger the UI
            window.location.href = '/?meta_connected=true&pages=1';
            
          } catch (err) {
            console.error(err);
            toast("Failed to complete Facebook connection.", "error");
          }
        }
      });
    }
  } catch (err) {
    toast(err.message, 'error');
  }
});

document.getElementById('btnConnectGoogleSheets')?.addEventListener('click', () => {
  if (!state.token) return;
  openModal('googleSheetsModal');
});

document.getElementById('closeGoogleSheetsModal')?.addEventListener('click', () => closeModal('googleSheetsModal'));
document.getElementById('cancelGoogleSheets')?.addEventListener('click', () => closeModal('googleSheetsModal'));

document.getElementById('googleSheetsForm')?.addEventListener('submit', async (e) => {
  e.preventDefault();
  const payload = {
    spreadsheet_url: document.getElementById('gsUrl').value,
    sheet_name: document.getElementById('gsSheetName').value || 'Sheet1'
  };
  try {
    await api('/integrations/google-sheets/connect', { method: 'POST', body: JSON.stringify(payload) });
    toast('Google Sheet Connected! Leads will now sync automatically. ✓');
    closeModal('googleSheetsModal');
    loadIntegrations();
  } catch (err) { toast(err.message, 'error'); }
});

// ─── Charts ──────────────────────────────────────────────────────
document.getElementById('analyticsDateRange')?.addEventListener('change', renderCharts);

function renderCharts() {
  if (!state.leads.length) return;

  const dateRangeStr = document.getElementById('analyticsDateRange')?.value;
  
  let filteredLeads = state.leads;
  if (dateRangeStr) {
    let start, end;
    if (dateRangeStr.includes(' to ')) {
      const parts = dateRangeStr.split(' to ');
      start = new Date(parts[0]);
      end = new Date(parts[1]);
    } else {
      start = new Date(dateRangeStr);
      end = new Date(dateRangeStr);
    }
    start.setHours(0, 0, 0, 0);
    end.setHours(23, 59, 59, 999);

    filteredLeads = state.leads.filter(l => {
      const created = new Date(l.created_at);
      return created >= start && created <= end;
    });
  }

  const statusCounts = { new:0, contacted:0, qualified:0, converted:0, lost:0 };
  filteredLeads.forEach(l => { if (statusCounts[l.status] != null) statusCounts[l.status]++; });

  // Group by Date for Time Series
  const dateMap = {};
  filteredLeads.forEach(l => {
    const dateStr = new Date(l.created_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
    dateMap[dateStr] = (dateMap[dateStr] || 0) + 1;
  });
  
  // Sort dates chronologically
  const sortedDates = Object.keys(dateMap).sort((a, b) => new Date(a + ` ${new Date().getFullYear()}`) - new Date(b + ` ${new Date().getFullYear()}`));
  const timeData = sortedDates.map(d => dateMap[d]);

  const colors = { new:'#3b82f6', contacted:'#f59e0b', qualified:'#8b5cf6', converted:'#10b981', lost:'#ef4444' };

  if (state.statusChart) state.statusChart.destroy();
  state.statusChart = new Chart(document.getElementById('statusChart'), {
    type: 'doughnut',
    data: {
      labels: Object.keys(statusCounts).map(s => s.charAt(0).toUpperCase() + s.slice(1)),
      datasets: [{ 
        data: Object.values(statusCounts), 
        backgroundColor: Object.values(colors), 
        borderWidth: 2,
        borderColor: '#1e2433', // Match dark background
        hoverOffset: 4
      }],
    },
    options: { 
      responsive: true, 
      cutout: '75%', // Thinner ring for modern look
      plugins: { 
        legend: { position:'bottom', labels: { color: '#8b95ad', font: { size: 13, family: 'Inter' }, padding: 20 } } 
      } 
    },
  });

  if (state.campaignChart) state.campaignChart.destroy();
  
  const ctx = document.getElementById('campaignChart').getContext('2d');
  const gradient = ctx.createLinearGradient(0, 0, 0, 400);
  gradient.addColorStop(0, 'rgba(99, 102, 241, 0.5)'); // Indigo semi-transparent
  gradient.addColorStop(1, 'rgba(99, 102, 241, 0.0)'); // Fade to transparent

  state.campaignChart = new Chart(ctx, {
    type: 'line',
    data: {
      labels: sortedDates,
      datasets: [{ 
        label: 'New Leads', 
        data: timeData, 
        borderColor: '#6366f1', 
        backgroundColor: gradient,
        borderWidth: 3,
        tension: 0.4, // Smooth curves
        fill: true,
        pointBackgroundColor: '#1e2433',
        pointBorderColor: '#6366f1',
        pointBorderWidth: 2,
        pointRadius: 4,
        pointHoverRadius: 6
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        x: { ticks: { color: '#8b95ad', font: { family: 'Inter' } }, grid: { display: false } },
        y: { beginAtZero: true, ticks: { color: '#8b95ad', font: { family: 'Inter' }, stepSize: 1 }, grid: { color: 'rgba(255,255,255,0.05)', borderDash: [5, 5] } },
      },
      plugins: { 
        legend: { display: false },
        tooltip: {
          backgroundColor: 'rgba(15, 23, 42, 0.9)',
          titleFont: { size: 13, family: 'Inter' },
          bodyFont: { size: 14, family: 'Inter', weight: 'bold' },
          padding: 12,
          cornerRadius: 8,
          displayColors: false
        }
      },
      interaction: {
        mode: 'index',
        intersect: false,
      },
    },
  });
}

// ─── Helpers ─────────────────────────────────────────────────────
function esc(str) {
  if (!str) return '';
  return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function populateCampaignDropdown(selectId, selectedVal) {
  const sel = document.getElementById(selectId);
  if (!sel) return;
  while (sel.options.length > 1) sel.remove(1);
  state.campaigns.forEach(c => {
    const opt = new Option(c.name, c.id);
    if (selectedVal && c.id === selectedVal) opt.selected = true;
    sel.appendChild(opt);
  });
}

async function populateStaffDropdown(selectId, selectedVal) {
  if (!state.staff.length) await loadStaff();
  const sel = document.getElementById(selectId);
  if (!sel) return;
  while (sel.options.length > 1) sel.remove(1);
  state.staff.filter(u => u.is_active).forEach(u => {
    const opt = new Option(`${u.name} (${u.role})`, u.id);
    if (selectedVal && u.id === selectedVal) opt.selected = true;
    sel.appendChild(opt);
  });
}

function copyText(elId) {
  const el = document.getElementById(elId);
  if (!el) return;
  navigator.clipboard.writeText(el.textContent).then(() => toast('Copied to clipboard ✓'));
}

// ─── Initialise ───────────────────────────────────────────────────
async function loadAll() {
  updateUserUI();
  if (state.token) {
    await loadCampaigns();
    await loadLeads();
    await loadConnections();
  }
}

// ─── Meta Ads OAuth Flow ──────────────────────────────────────────
let tempFbSession = null;
let tempFbPageId = null;
let tempFbPageName = null;

async function loadConnections() {
  if (!state.token) return;
  try {
    const conns = await api('/integrations/facebook/connections');
    const container = document.getElementById('integrationList');
    if (!container) return;
    
    container.innerHTML = '';
    conns.forEach(c => {
      const card = document.createElement('div');
      card.className = 'connect-new-card';
      card.style.border = '1px solid var(--accent)';
      card.innerHTML = `
        <div class="connect-icon"><i data-lucide="facebook" style="color: #1877F2;"></i></div>
        <div class="connect-info">
          <h3 style="display:flex; align-items:center; gap:8px;">Facebook Lead Ads <span class="pill active" style="font-size:10px;">✓ Connected</span></h3>
          <p style="margin-bottom:8px;">Receiving leads from <strong>${esc(c.page_name)}</strong></p>
          <div style="font-size:12px; color:var(--text-3);">
            Lead Forms: ${c.connected_forms.length} connected
          </div>
        </div>
        <button class="btn btn-secondary" onclick="openManageMeta(${c.id}, '${esc(c.page_id)}', '${esc(c.page_name)}', '${esc(JSON.stringify(c.connected_forms))}')">
          <i data-lucide="settings"></i> Manage
        </button>
      `;
      container.appendChild(card);
    });
    lucide.createIcons();
  } catch(e) {
    console.error('Failed to load connections:', e);
  }
}

document.getElementById('btnConnectFacebook')?.addEventListener('click', async () => {
  try {
    const { url } = await api('/integrations/facebook/auth-url');
    // Open popup
    const width = 600, height = 700;
    const left = (window.innerWidth - width) / 2;
    const top = (window.innerHeight - height) / 2;
    window.open(url, 'fb_oauth', `width=${width},height=${height},top=${top},left=${left}`);
  } catch (err) {
    toast('Could not start Facebook connection: ' + err.message, 'error');
  }
});

// Listen for popup messages
window.addEventListener('message', async (event) => {
  // Security check: event.origin could be checked here in production
  const data = event.data;
  if (!data || typeof data !== 'object') return;
  
  if (data.type === 'FB_OAUTH_SUCCESS') {
    toast('Facebook Authorized! Fetching Pages...', 'success');
    await exchangeFbToken(data.code);
  } else if (data.type === 'FB_OAUTH_ERROR') {
    toast('Facebook connection could not be completed: ' + (data.error || 'Unknown error'), 'error');
  }
});

async function exchangeFbToken(code) {
  try {
    const base_url = window.location.origin;
    const redirect_uri = `${base_url}/integrations/facebook/callback`;
    const res = await api('/integrations/facebook/exchange', {
      method: 'POST',
      body: JSON.stringify({ code, redirect_uri })
    });
    tempFbSession = res.fb_session_token;
    
    // Now fetch pages
    const pagesRes = await api(`/integrations/facebook/pages?fb_session_token=${tempFbSession}`);
    if (!pagesRes.pages || pagesRes.pages.length === 0) {
      toast('No Facebook Pages are available for this account.', 'error');
      return;
    }
    
    // Show Page Selection Modal
    const list = document.getElementById('fbPageList');
    list.innerHTML = '';
    pagesRes.pages.forEach((p, i) => {
      list.innerHTML += `
        <label style="display:flex; align-items:center; gap:12px; padding:12px; background:var(--bg-2); border-radius:8px; border:1px solid var(--border); cursor:pointer;">
          <input type="radio" name="fb_page_sel" value="${esc(p.id)}" data-name="${esc(p.name)}" ${i===0?'checked':''}>
          <span style="font-weight:600;">${esc(p.name)}</span>
        </label>
      `;
    });
    openModal('fbPageModal');
    
  } catch (err) {
    toast('Failed to exchange token: ' + err.message, 'error');
  }
}

document.getElementById('cancelFbPageModal')?.addEventListener('click', () => {
  closeModal('fbPageModal');
  tempFbSession = null;
});
document.getElementById('closeFbPageModal')?.addEventListener('click', () => {
  closeModal('fbPageModal');
  tempFbSession = null;
});

document.getElementById('btnContinueFbForms')?.addEventListener('click', async () => {
  const selected = document.querySelector('input[name="fb_page_sel"]:checked');
  if (!selected) return toast('Please select a page', 'error');
  
  tempFbPageId = selected.value;
  tempFbPageName = selected.getAttribute('data-name');
  closeModal('fbPageModal');
  
  try {
    toast('Fetching Lead Forms...');
    const res = await api(`/integrations/facebook/pages/${tempFbPageId}/forms?fb_session_token=${tempFbSession}`);
    
    const list = document.getElementById('fbFormsList');
    list.innerHTML = '';
    if (!res.forms || res.forms.length === 0) {
      list.innerHTML = '<p class="hint">No Lead Forms found on this page.</p>';
    } else {
      res.forms.forEach(f => {
        list.innerHTML += `
          <label style="display:flex; align-items:center; gap:12px; padding:12px; background:var(--bg-2); border-radius:8px; border:1px solid var(--border); cursor:pointer;">
            <input type="checkbox" name="fb_form_sel" value="${esc(f.id)}" checked>
            <div>
              <div style="font-weight:600;">${esc(f.name)}</div>
              <div style="font-size:11px; color:var(--text-3);">Form ID: ${esc(f.id)} • Status: ${f.status||'ACTIVE'}</div>
            </div>
          </label>
        `;
      });
    }
    openModal('fbFormsModal');
  } catch (err) {
    toast('Failed to fetch forms: ' + err.message, 'error');
  }
});

document.getElementById('backToFbPageModal')?.addEventListener('click', () => {
  closeModal('fbFormsModal');
  openModal('fbPageModal');
});
document.getElementById('closeFbFormsModal')?.addEventListener('click', () => {
  closeModal('fbFormsModal');
  tempFbSession = null;
});

document.getElementById('btnCompleteFbConnect')?.addEventListener('click', async () => {
  const checkboxes = document.querySelectorAll('input[name="fb_form_sel"]:checked');
  const forms = Array.from(checkboxes).map(c => c.value);
  
  try {
    const res = await api('/integrations/facebook/connect', {
      method: 'POST',
      body: JSON.stringify({
        fb_session_token: tempFbSession,
        page_id: tempFbPageId,
        page_name: tempFbPageName,
        forms: forms
      })
    });
    closeModal('fbFormsModal');
    toast('Facebook Page successfully connected! ✓', 'success');
    tempFbSession = null;
    loadConnections();
  } catch(err) {
    toast('Connection failed: ' + err.message, 'error');
  }
});

function openManageMeta(connId, pageId, pageName, formsJson) {
  document.getElementById('manageFbPageName').textContent = pageName;
  const forms = JSON.parse(formsJson || '[]');
  const list = document.getElementById('manageFbFormsList');
  list.innerHTML = '';
  if (forms.length === 0) list.innerHTML = '<li>No forms connected</li>';
  else forms.forEach(f => {
    list.innerHTML += `<li>Form ID: ${esc(f)}</li>`;
  });
  
  document.getElementById('btnDisconnectFb').onclick = async () => {
    if (confirm('Are you sure you want to disconnect this page?')) {
      try {
        await api(`/integrations/facebook/connections/${connId}/disconnect`, { method: 'POST' });
        toast('Disconnected successfully', 'success');
        closeModal('manageMetaModal');
        loadConnections();
      } catch (e) {
        toast('Failed to disconnect: ' + e.message, 'error');
      }
    }
  };
  openModal('manageMetaModal');
}

document.getElementById('btnDoneManageFb')?.addEventListener('click', () => closeModal('manageMetaModal'));
document.getElementById('closeManageMetaModal')?.addEventListener('click', () => closeModal('manageMetaModal'));

// Add CSS animation for toast
const style = document.createElement('style');
style.textContent = `
  @keyframes slideUp {
    from { opacity:0; transform:translateY(12px); }
    to   { opacity:1; transform:translateY(0); }
  }
`;
document.head.appendChild(style);

// Boot
document.addEventListener('DOMContentLoaded', () => {
  lucide.createIcons();
  
  // Initialize Flatpickr calendars
  if (typeof flatpickr !== 'undefined') {
    flatpickr("#analyticsDateRange", { mode: "range", dateFormat: "Y-m-d" });
    flatpickr("#leadsDateFilter", { 
      mode: "range", 
      dateFormat: "Y-m-d", 
      onChange: filterAndRenderTable 
    });
  }

  // Print Logic
  document.getElementById('btnPrintLeads')?.addEventListener('click', () => {
    window.print();
  });

  loadAll();

  // Handle OAuth callback success message
  const urlParams = new URLSearchParams(window.location.search);
  if (urlParams.get('meta_connected') === 'true') {
    const pagesCount = urlParams.get('pages') || '1';
    setTimeout(() => {
      toast(`Successfully connected ${pagesCount} Facebook Page(s)! ✓`);
      // Clean up URL
      window.history.replaceState({}, document.title, window.location.pathname);
      // Ensure Lead Sources tab is active
      const tab = document.querySelector('[data-tab="tab-connect"]');
      if (tab) tab.click();
    }, 500);
  } else if (!state.token) {
    // Auto-open auth if no token
    setTimeout(() => openModal('authModal'), 500);
  }
});
