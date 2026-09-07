/* ═══════════════════════════════════════════════════════════════
   MetaCRM — App Logic
   ═══════════════════════════════════════════════════════════════ */

// ─── Global Error Catchers ────────────────────────────────────────
window.onerror = function(msg, src, line, col, err) {
  console.error('[GlobalError]', msg, '\n  Source:', src, 'Line:', line, 'Col:', col, '\n  Error:', err);
};
window.addEventListener('unhandledrejection', function(event) {
  console.error('[UnhandledPromise]', event.reason);
});

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
  // Pagination
  currentPage: 0,
  pageSize: 100,
  totalLeads: 0,
  // Active date filter
  dateFrom: null,   // 'YYYY-MM-DD' or null
  dateTo: null,
  activeChip: 'today',
};

// ─── API Helper ──────────────────────────────────────────────────
async function api(path, opts = {}) {
  const method = (opts.method || 'GET').toUpperCase();
  console.log(`[API] ${method} ${path}`);
  const headers = { 'Content-Type': 'application/json', ...(opts.headers || {}) };
  if (state.token) headers['Authorization'] = `Bearer ${state.token}`;
  const res = await fetch(path, { ...opts, headers });
  if (res.status === 401) { console.warn('[API] 401 Unauthorized — logging out'); doLogout(); return null; }
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    const errMsg = err.detail || `HTTP ${res.status}`;
    console.error(`[API Error] ${method} ${path} →`, res.status, errMsg, err);
    throw new Error(errMsg);
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
function openModal(id) {
  console.log('[Modal] Opening:', id);
  const el = document.getElementById(id);
  if (!el) { console.warn('[Modal] Element not found:', id); return; }
  el.classList.add('active');
}
function closeModal(id) {
  console.log('[Modal] Closing:', id);
  const el = document.getElementById(id);
  if (!el) { console.warn('[Modal] Element not found:', id); return; }
  el.classList.remove('active');
}

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
  } catch (err) { console.error('[Login Error]', err); toast(err.message, 'error'); }
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
  } catch (err) { console.error('[Register Error]', err); toast(err.message, 'error'); }
});

// ─── Leads ───────────────────────────────────────────────────────
// ─── Date chip helpers ───────────────────────────────────────────
function toISODate(d) {
  return d.toISOString().slice(0, 10);
}

function setLeadDateChip(chip) {
  state.activeChip = chip;
  state.currentPage = 0;
  const today = new Date();
  const todayStr = toISODate(today);

  // Update chip active styles
  ['chipToday','chipYesterday','chipWeek','chipAll'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.classList.remove('active');
  });
  const activeId = { today:'chipToday', yesterday:'chipYesterday', week:'chipWeek', all:'chipAll' }[chip];
  if (activeId) document.getElementById(activeId)?.classList.add('active');

  // Clear the flatpickr custom input
  const fp = document.getElementById('leadsDateFilter')?._flatpickr;
  if (fp) fp.clear();

  if (chip === 'today') {
    state.dateFrom = todayStr;
    state.dateTo   = todayStr;
  } else if (chip === 'yesterday') {
    const y = new Date(today); y.setDate(y.getDate() - 1);
    state.dateFrom = toISODate(y);
    state.dateTo   = toISODate(y);
  } else if (chip === 'week') {
    const mon = new Date(today);
    mon.setDate(today.getDate() - ((today.getDay() + 6) % 7));
    state.dateFrom = toISODate(mon);
    state.dateTo   = todayStr;
  } else {
    state.dateFrom = null;
    state.dateTo   = null;
  }
  loadLeads();
}

async function loadLeads() {
  if (!state.token) { renderKanban([]); renderTable([], 0); return; }
  try {
    const params = new URLSearchParams();
    const search = document.getElementById('searchInput').value.trim();
    const campaign = document.getElementById('campaignFilterSelect').value;
    if (search)   params.append('search', search);
    if (campaign) params.append('campaign_id', campaign);
    if (state.dateFrom) params.append('date_from', state.dateFrom);
    if (state.dateTo)   params.append('date_to',   state.dateTo);
    params.append('limit',  state.pageSize);
    params.append('offset', state.currentPage * state.pageSize);

    // Use raw fetch so we can read the X-Total-Count header
    const headers = { 'Content-Type': 'application/json' };
    if (state.token) headers['Authorization'] = `Bearer ${state.token}`;
    const res = await fetch(`/leads?${params}`, { headers });
    if (res.status === 401) { doLogout(); return; }
    if (!res.ok) throw new Error(`HTTP ${res.status}`);

    state.totalLeads = parseInt(res.headers.get('X-Total-Count') || '0', 10);
    state.leads = await res.json() || [];

    renderKanban(state.leads);
    renderTable(state.leads, state.totalLeads);
    renderPagination();
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
  // In-memory filter is now only used when flatpickr custom range is set
  // (chip-based filters go to the server via loadLeads)
  renderTable(state.leads, state.totalLeads);
}


function renderTable(leads, total) {
  const tbody = document.getElementById('leadsTableBody');
  if (!leads.length) {
    const msg = (state.dateFrom || state.dateTo)
      ? `No leads found for the selected date range.`
      : `No leads found.`;
    tbody.innerHTML = `<tr><td colspan="9" style="text-align:center;padding:40px;color:var(--text-3);">${msg}</td></tr>`;
    return;
  }
  tbody.innerHTML = leads.map(l => {
    const dt = l.created_at ? new Date(l.created_at) : null;
    const dateStr = dt ? dt.toLocaleDateString('en-GB', { day:'2-digit', month:'short', year:'numeric' }) : '—';
    const timeStr = dt ? dt.toLocaleTimeString('en-GB', { hour:'2-digit', minute:'2-digit', hour12: true }) : '';
    const campaign = state.campaigns.find(c => c.id === l.campaign_id);
    const assignee = campaign?.assigned_user_name || l.campaign_name ? (campaign?.assigned_user_name || '—') : '—';
    const formName = l.form_name || '—';
    const statusClass = l.status.replace(' ', '-');
    return `
      <tr>
        <td class="col-id" style="color:var(--text-3);font-size:11px;">#${l.id}</td>
        <td><strong>${esc(l.name || '—')}</strong></td>
        <td class="col-contact"><div style="font-size:12px;color:var(--text-2);line-height:1.6;">${esc(l.email || '—')}<br>${esc(l.phone || '')}</div></td>
        <td class="col-campaign"><span class="lead-tag">${esc(l.campaign_name || '—')}</span></td>
        <td class="col-form" style="font-size:12px;color:var(--text-2);">${esc(formName)}</td>
        <td class="col-assigned" style="font-size:12px;color:var(--text-2);">${esc(assignee)}</td>
        <td><span class="pill ${statusClass}">${l.status}</span></td>
        <td class="col-date">
          <div style="font-size:12px;color:var(--text-2);line-height:1.6;">
            ${dateStr}<br>
            <span style="color:var(--text-3);font-size:11px;">${timeStr}</span>
          </div>
        </td>
        <td>
          <button class="btn btn-ghost" style="height:30px;padding:0 10px;font-size:12px;" onclick="openLeadDrawerById(${l.id})">
            Open
          </button>
        </td>
      </tr>`;
  }).join('');
}

function renderPagination() {
  const container = document.getElementById('leadsTablePagination');
  if (!container) return;

  const total = state.totalLeads;
  const size  = state.pageSize;
  const page  = state.currentPage;
  const totalPages = Math.max(1, Math.ceil(total / size));

  if (total <= size) {
    container.innerHTML = `<span class="pagination-info">Showing ${total.toLocaleString()} lead${total !== 1 ? 's' : ''}</span>`;
    return;
  }

  const start = page * size + 1;
  const end   = Math.min(page * size + size, total);

  container.innerHTML = `
    <button class="btn btn-ghost pagination-btn" id="pagePrev" ${page === 0 ? 'disabled' : ''}>
      <i data-lucide="chevron-left"></i> Prev
    </button>
    <span class="pagination-info">
      ${start.toLocaleString()}–${end.toLocaleString()} of <strong>${total.toLocaleString()}</strong> leads
      &nbsp;·&nbsp; Page ${page + 1} of ${totalPages}
    </span>
    <button class="btn btn-ghost pagination-btn" id="pageNext" ${page >= totalPages - 1 ? 'disabled' : ''}>
      Next <i data-lucide="chevron-right"></i>
    </button>
  `;
  lucide.createIcons();

  document.getElementById('pagePrev')?.addEventListener('click', () => {
    if (state.currentPage > 0) { state.currentPage--; loadLeads(); }
  });
  document.getElementById('pageNext')?.addEventListener('click', () => {
    if (state.currentPage < totalPages - 1) { state.currentPage++; loadLeads(); }
  });
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
  } catch (err) { console.error('[UpdateStatus Error]', err); toast(err.message, 'error'); }
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
  } catch (err) { console.error('[AssignLead Error]', err); toast(err.message, 'error'); }
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
  } catch (err) { console.error('[SendNote Error]', err); toast(err.message, 'error'); }
});

// ─── Search / Filter ──────────────────────────────────────────────
let searchTimer;
document.getElementById('searchInput').addEventListener('input', () => {
  clearTimeout(searchTimer);
  state.currentPage = 0;
  searchTimer = setTimeout(loadLeads, 300);
});
document.getElementById('campaignFilterSelect').addEventListener('change', () => {
  state.currentPage = 0;
  loadLeads();
});

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
  } catch (err) { console.error('[AddCampaign Error]', err); toast(err.message, 'error'); }
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
  } catch (err) { console.error('[EditCampaign Error]', err); toast(err.message, 'error'); }
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
  } catch (err) { console.error('[AddStaff Error]', err); toast(err.message, 'error'); }
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
  } catch (err) { console.error('[UpdateStaff Error]', err); toast(err.message, 'error'); }
}
async function activateStaff(id) {
  try {
    await api(`/staff/${id}/activate`, { method:'PATCH' });
    toast('Staff member activated ✓');
    loadStaff();
  } catch (err) { console.error('[DeleteStaff Error]', err); toast(err.message, 'error'); }
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
  } catch (err) { console.error('[ExportLeads Error]', err); toast(err.message, 'error'); }
});

// ─── Integrations & Lead Sources ─────────────────────────────────
async function loadIntegrations() {
  if (!state.token || !state.user || state.user.role !== 'admin') return;
  try {
    const list = document.getElementById('integrationList');
    if (!list) return;
    list.innerHTML = '';
    
    let html = '';
    let isMetaConnected = false;
    
    // 1. Fetch Facebook Connections
    try {
      const fbConns = await api('/integrations/facebook/connections');
      const fbConnectCard = document.getElementById('fbConnectCard');
      if (fbConnectCard) {
        fbConnectCard.style.display = fbConns.length > 0 ? 'none' : 'flex';
      }
      
      if (fbConns && fbConns.length > 0) {
        isMetaConnected = true;
      }

      fbConns.forEach(c => {
        html += `
          <div class="connect-new-card" style="border: 1px solid var(--accent);">
            <div class="connect-icon"><svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#1877F2" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="lucide lucide-facebook"><path d="M18 2h-3a5 5 0 0 0-5 5v3H7v4h3v8h4v-8h3l1-4h-4V7a1 1 0 0 1 1-1h3z"/></svg></div>
            <div class="connect-info">
              <h3 style="display:flex; align-items:center; gap:8px;">Facebook Lead Ads <span class="pill active" style="font-size:10px;">✓ Connected</span></h3>
              <p style="margin-bottom:8px;">Receiving leads from <strong>${esc(c.page_name)}</strong></p>
              <div style="font-size:12px; color:var(--text-3);">
                Lead Forms: ${c.connected_forms.length} connected
              </div>
            </div>
            <button class="btn btn-secondary" style="color: var(--red); border-color: var(--red);" onclick="disconnectMetaCard(${c.id})">
              <i data-lucide="trash-2"></i> Remove
            </button>
          </div>
        `;
      });
    } catch(e) { console.error('FB Conns failed', e); }
    
    // 2. Fetch Google Sheets Status
    try {
      const gsRes = await api('/integrations/google-sheets/status');
      const gsConnectCard = document.getElementById('googleSheetsConnectCard');
      if (gsConnectCard) {
        gsConnectCard.style.display = (gsRes && gsRes.connected) ? 'none' : 'flex';
      }

      if (gsRes && gsRes.connected) {
        html += `
          <div class="integration-card" style="margin-top:12px;">
            <div class="integration-card-left">
              <div class="integration-icon"><i data-lucide="file-spreadsheet" style="color: #0F9D58;"></i></div>
              <div class="integration-info">
                <h3>Google Sheets Sync <span class="integration-status">Active</span></h3>
                <p>Syncing to <a href="${esc(gsRes.spreadsheet_url)}" target="_blank">Spreadsheet</a> (${esc(gsRes.sheet_name)})</p>
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
          } catch (err) { console.error('[OrgSettings Error]', err); toast(err.message, 'error'); }
        });
      }
    }

    const dot = document.querySelector('.badge-dot');
    const statusText = document.getElementById('connectionStatus');
    if (dot) { dot.className = `badge-dot ${isMetaConnected ? 'connected' : 'disconnected'}`; }
    if (statusText) statusText.textContent = isMetaConnected ? 'Connected ✓' : 'Not Connected';

  } catch (err) { console.error('Integrations failed:', err); }
}

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
  } catch (err) { console.error('[GoogleSheets Error]', err); toast(err.message, 'error'); }
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
    // Default to today's leads on every fresh load
    const todayStr = toISODate(new Date());
    state.dateFrom = todayStr;
    state.dateTo   = todayStr;
    state.activeChip = 'today';
    await loadCampaigns();
    await loadLeads();
    await loadIntegrations();
  }
}

// ─── Meta Ads OAuth Flow ──────────────────────────────────────────
let tempFbSession = null;
let tempFbPageId = null;
let tempFbPageName = null;



document.getElementById('btnConnectFacebook')?.addEventListener('click', async () => {
  try {
    const { url } = await api('/integrations/facebook/auth-url');
    // Open popup
    const width = 600, height = 700;
    const left = (window.innerWidth - width) / 2;
    const top = (window.innerHeight - height) / 2;
    window.open(url, 'fb_oauth', `width=${width},height=${height},top=${top},left=${left}`);
  } catch (err) {
    console.error('[FbConnect Error]', err);
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
      toast('No Facebook Pages found. Make sure you selected your Page during Facebook login and have Admin access.', 'error');
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
    console.error('[ExchangeToken Error]', err);
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
    console.error('[FetchForms Error]', err);
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
    loadIntegrations();
  } catch(err) {
    console.error('[CompleteFbConnect Error]', err);
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
        loadIntegrations();
      } catch (e) {
        console.error('[Disconnect Error]', e);
        toast('Failed to disconnect: ' + e.message, 'error');
      }
    }
  };
  openModal('manageMetaModal');
}

window.disconnectMetaCard = async function(connId) {
  if (confirm('Are you sure you want to disconnect this Facebook Page? Leads will stop syncing immediately.')) {
    try {
      await api(`/integrations/facebook/connections/${connId}/disconnect`, { method: 'POST' });
      toast('Facebook account disconnected successfully', 'success');
      loadIntegrations();
    } catch (e) {
      console.error('[DisconnectCard Error]', e);
      toast('Failed to disconnect: ' + e.message, 'error');
    }
  }
};

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
      onChange: function(selectedDates, dateStr) {
        if (selectedDates.length === 0) return;
        // Deactivate chips
        ['chipToday','chipYesterday','chipWeek','chipAll'].forEach(id => {
          document.getElementById(id)?.classList.remove('active');
        });
        state.activeChip = null;
        state.currentPage = 0;
        if (selectedDates.length === 1) {
          state.dateFrom = toISODate(selectedDates[0]);
          state.dateTo   = toISODate(selectedDates[0]);
        } else if (selectedDates.length === 2) {
          state.dateFrom = toISODate(selectedDates[0]);
          state.dateTo   = toISODate(selectedDates[1]);
        }
        loadLeads();
      }
    });
  }

  // Print Logic
  document.getElementById('btnPrintLeads')?.addEventListener('click', () => {
    window.print();
  });

  // Sidebar Toggle for Mobile
  const sidebarToggleBtn = document.getElementById('sidebarToggleBtn');
  const sidebar = document.getElementById('sidebar');
  const sidebarBackdrop = document.getElementById('sidebarBackdrop');

  if (sidebarToggleBtn && sidebar && sidebarBackdrop) {
    function toggleSidebar() {
      sidebar.classList.toggle('open');
      sidebarBackdrop.classList.toggle('active');
    }
    sidebarToggleBtn.addEventListener('click', toggleSidebar);
    sidebarBackdrop.addEventListener('click', toggleSidebar);
    
    // Close sidebar when clicking a nav item on mobile
    const navItems = sidebar.querySelectorAll('.nav-item');
    navItems.forEach(item => {
      item.addEventListener('click', () => {
        if (window.innerWidth <= 900) {
          sidebar.classList.remove('open');
          sidebarBackdrop.classList.remove('active');
        }
      });
    });
  }

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
