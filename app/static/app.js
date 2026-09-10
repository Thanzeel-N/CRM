/* ═══════════════════════════════════════════════════════════════
   MetaCRM — App Logic
   ═══════════════════════════════════════════════════════════════ */

// ─── Theme Init (before paint) ───────────────────────────────────
(function() {
  var saved = localStorage.getItem('crm_theme') || 'dark';
  document.documentElement.setAttribute('data-theme', saved);
})();

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
  hideInactiveCampaigns: false,
  // Pagination
  currentPage: 0,
  pageSize: 100,
  totalLeads: 0,
  // Active date filter
  dateFrom: null,   // 'YYYY-MM-DD' or null
  dateTo: null,
  activeChip: 'all',
  // Theme
  theme: localStorage.getItem('crm_theme') || 'dark',
};

// ─── API Helper ──────────────────────────────────────────────────
async function api(path, opts = {}) {
  const method = (opts.method || 'GET').toUpperCase();
  const headers = { 'Content-Type': 'application/json', ...(opts.headers || {}) };
  if (state.token) headers['Authorization'] = `Bearer ${state.token}`;
  const res = await fetch(path, { ...opts, headers });
  if (res.status === 401) { toast('Session expired. Please sign in again.', 'error'); doLogout(); return null; }
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    const errMsg = err.detail || `HTTP ${res.status}`;
    throw new Error(errMsg);
  }
  if (res.status === 204) return null;
  return res.json();
}

function toast(msg, type = 'success') {
  const el = document.createElement('div');
  el.setAttribute('role', 'alert');
  el.setAttribute('aria-live', 'polite');
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
  const duration = type === 'error' ? 5000 : 3500;
  setTimeout(() => el.remove(), duration);
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
  resetWorkflow();
  state.token = null;
  state.user  = null;
  state.leads = []; state.campaigns = []; state.staff = [];
  closeDrawer();
  renderKanban([]); renderTable([], 0);
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
    const tab = item.dataset.tab;
    // Require login only for admin-only management tabs
    const adminTabs = ['tab-campaigns', 'tab-staff', 'tab-connect', 'tab-simulator'];
    if (adminTabs.includes(tab) && state.user?.role !== 'admin') { toast('Admin access required', 'error'); return; }
    if (!state.user && adminTabs.includes(tab)) { openModal('authModal'); return; }
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
    item.classList.add('active');
    document.getElementById(tab)?.classList.add('active');
    document.getElementById('pageTitleText').textContent = item.querySelector('span')?.textContent || '';

    if (tab === 'tab-analytics') { renderCharts(); loadWorkflow(); }
    if (tab === 'tab-today') loadWorkflow();
    if (tab === 'tab-connect') loadIntegrationJobs();
    if (tab === 'tab-campaigns') loadCampaigns();
    if (tab === 'tab-staff')     loadStaff();
    if (tab === 'tab-connect')   loadIntegrations();
  });
});

// ─── Modals ──────────────────────────────────────────────────────
function openModal(id) {
  const el = document.getElementById(id);
  if (!el) return;
  el.classList.add('active');
  const firstInput = el.querySelector('input:not([type="hidden"]), select, textarea');
  if (firstInput) setTimeout(() => firstInput.focus(), 100);
}
function closeModal(id) {
  const el = document.getElementById(id);
  if (!el) return;
  el.classList.remove('active');
}

document.querySelectorAll('.modal-backdrop').forEach(m => {
  m.addEventListener('click', e => { if (e.target === m) m.classList.remove('active'); });
});

// Escape key closes active modal
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') {
    const activeModal = document.querySelector('.modal-backdrop.active');
    if (activeModal) activeModal.classList.remove('active');
    const activeDrawer = document.getElementById('leadDrawer');
    if (activeDrawer && activeDrawer.classList.contains('active')) closeDrawer();
  }
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

// Logout
document.getElementById('logoutBtn').addEventListener('click', doLogout);

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
  const newTheme = isDark ? 'light' : 'dark';
  html.setAttribute('data-theme', newTheme);
  state.theme = newTheme;
  localStorage.setItem('crm_theme', newTheme);
  document.getElementById('themeIcon').setAttribute('data-lucide', isDark ? 'sun' : 'moon');
  lucide.createIcons();
  if (state.leads.length > 0) renderCharts();
});

// Sidebar toggle (mobile) — removed top-level duplicate, handled in DOMContentLoaded

// ─── Login / Register ─────────────────────────────────────────────
document.getElementById('loginForm').addEventListener('submit', async e => {
  e.preventDefault();
  const btn = e.target.querySelector('button[type="submit"]');
  if (btn) { btn.disabled = true; btn.innerHTML = '<i data-lucide="loader" class="spin"></i> Signing in...'; lucide.createIcons(); }
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
  finally { if (btn) { btn.disabled = false; btn.innerHTML = '<i data-lucide="log-in"></i> Sign In'; lucide.createIcons(); } }
});

document.getElementById('registerForm').addEventListener('submit', async e => {
  e.preventDefault();
  const btn = e.target.querySelector('button[type="submit"]');
  if (btn) { btn.disabled = true; btn.innerHTML = '<i data-lucide="loader" class="spin"></i> Creating...'; lucide.createIcons(); }
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
  finally { if (btn) { btn.disabled = false; btn.innerHTML = '<i data-lucide="building"></i> Create Account'; lucide.createIcons(); } }
});

// ─── Leads ───────────────────────────────────────────────────────
// ─── Date chip helpers ───────────────────────────────────────────
function toISODate(d) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
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
    const formName = document.getElementById('formFilterSelect').value;
    if (search)   params.append('search', search);
    if (campaign) params.append('campaign_id', campaign);
    if (formName) params.append('form_name', formName);
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
  } catch (err) { toast('Load leads failed: ' + err.message, 'error'); }
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
    try {
      const campData = await api('/leads/campaigns') || [];
      const sel = document.getElementById('campaignFilterSelect');
      const current = sel.value;
      // Clear options except first
      while (sel.options.length > 1) sel.remove(1);
      campData.forEach(c => {
        const opt = document.createElement('option');
        const cVal = typeof c === 'object' ? (c.name || c.id) : c;
        const cName = typeof c === 'object' ? (c.name || c.id) : c;
        opt.value = cVal;
        opt.textContent = cName;
        if (String(cVal) === current) opt.selected = true;
        sel.appendChild(opt);
      });
    } catch (e) { }
    // form filter
    try {
      const forms = await api('/leads/forms') || [];
      const formSel = document.getElementById('formFilterSelect');
      const currentForm = formSel.value;
      while (formSel.options.length > 1) formSel.remove(1);
      forms.forEach(f => {
        const opt = document.createElement('option');
        const fVal = typeof f === 'object' ? (f.name || f.id) : f;
        const fName = typeof f === 'object' ? (f.name || f.id) : f;
        opt.value = fVal;
        opt.textContent = fName;
        if (fVal === currentForm) opt.selected = true;
        formSel.appendChild(opt);
      });
    } catch (e) { }

  } catch (err) { }
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
  const thead = document.querySelector('.data-table thead tr');
  const selectedForm = document.getElementById('formFilterSelect').value;

  // Determine dynamic columns based on selected form
  let dynamicCols = [];
  if (selectedForm && leads.length > 0) {
    leads.forEach(l => {
      if (l.raw_data && l.raw_data.field_data) {
        l.raw_data.field_data.forEach(fd => {
          const n = fd.name;
          if (n !== 'email' && n !== 'full_name' && n !== 'phone_number' && n !== 'name') {
            if (!dynamicCols.includes(n)) dynamicCols.push(n);
          }
        });
      }
    });
  }

  // Update table header
  if (thead) {
    let headerHtml = `
      <th class="col-id">#</th>
      <th>Lead</th>
      <th class="col-contact">Contact</th>
      <th class="col-campaign">Campaign</th>
      <th class="col-form">Lead Form</th>
      <th class="col-assigned">Assigned To</th>
      <th>Status</th>
      <th class="col-date">Date Received</th>
    `;
    dynamicCols.forEach(col => {
      const label = col.replace(/_/g, ' ').replace(/\b\w/g, char => char.toUpperCase());
      headerHtml += `<th class="col-dynamic" title="${esc(col)}">${esc(label)}</th>`;
    });
    headerHtml += `<th>Action</th>`;
    thead.innerHTML = headerHtml;
  }

  if (!leads.length) {
    const search = document.getElementById('searchInput')?.value.trim();
    const campaign = document.getElementById('campaignFilterSelect')?.value;
    const isFiltered = state.dateFrom || state.dateTo || selectedForm || search || campaign;
    const msg = isFiltered
      ? `No leads found matching current filter criteria.`
      : `No leads found.`;
    tbody.innerHTML = `<tr><td colspan="${9 + dynamicCols.length}" style="text-align:center;padding:40px;color:var(--text-3);">${msg}</td></tr>`;
    return;
  }
  
  tbody.innerHTML = leads.map(l => {
    try {
      const dt = l.created_at ? new Date(l.created_at) : null;
      const isValidDt = dt && !isNaN(dt.getTime());
      const dateStr = isValidDt ? dt.toLocaleDateString('en-GB', { day:'2-digit', month:'short', year:'numeric' }) : '—';
      const timeStr = isValidDt ? dt.toLocaleTimeString('en-GB', { hour:'2-digit', minute:'2-digit', hour12: true }) : '';
      const campaign = state.campaigns.find(c => c.id === l.campaign_id);
      const assignee = l.owner_name || campaign?.assigned_user_name || '—';
      const formName = l.form_name || '—';
      const statusText = String(l.status || 'new');
      const statusClass = statusText.replace(/ /g, '-');

      let rowHtml = `
        <tr class="lead-row" onclick="openLeadDrawerById(${l.id})" style="cursor:pointer;">
          <td class="col-id" style="color:var(--text-3);font-size:11px;">#${l.id || ''}</td>
          <td><strong>${esc(l.name || '—')}</strong></td>
          <td class="col-contact"><div style="font-size:12px;color:var(--text-2);line-height:1.6;">${esc(l.email || '—')}<br>${esc(l.phone || '')}</div></td>
          <td class="col-campaign"><span class="lead-tag">${esc(l.campaign_name || '—')}</span></td>
          <td class="col-form" style="font-size:12px;color:var(--text-2);">${esc(formName)}</td>
          <td class="col-assigned" style="font-size:12px;color:var(--text-2);">${esc(assignee)}</td>
          <td><span class="pill ${statusClass}">${esc(statusText)}</span></td>
          <td class="col-date">
            <div style="font-size:12px;color:var(--text-2);line-height:1.6;">
              ${dateStr}<br>
              <span style="color:var(--text-3);font-size:11px;">${timeStr}</span>
            </div>
          </td>
      `;

      dynamicCols.forEach(col => {
        let val = '—';
        if (l.raw_data && l.raw_data.field_data && Array.isArray(l.raw_data.field_data)) {
          const field = l.raw_data.field_data.find(f => f.name === col);
          if (field && field.values && Array.isArray(field.values) && field.values.length > 0) {
            val = field.values.join(', ');
          }
        }
        rowHtml += `<td class="col-dynamic" style="font-size:12px;color:var(--text-2);white-space:nowrap;max-width:220px;overflow:hidden;text-overflow:ellipsis;" title="${esc(val)}">${esc(val)}</td>`;
      });

      rowHtml += `
          <td>
            <button class="btn btn-ghost" style="height:30px;padding:0 10px;font-size:12px;display:inline-flex;align-items:center;gap:4px;" onclick="event.stopPropagation(); openLeadDrawerById(${l.id})">
              <i data-lucide="panel-right" style="width:14px;height:14px;"></i> Details
            </button>
          </td>
        </tr>
      `;
      return rowHtml;
    } catch (err) { }
  }).join('');

  lucide.createIcons();
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
  loadLeadWorkflow(lead);
  document.getElementById('drawerLeadName').textContent    = lead.name || '—';
  document.getElementById('drawerLeadTime').textContent    = lead.created_at ? new Date(lead.created_at).toLocaleString() : '';
  document.getElementById('drawerLeadEmail').textContent   = lead.email || '—';
  document.getElementById('drawerLeadPhone').textContent   = lead.phone || '—';
  document.getElementById('drawerLeadCampaign').textContent = lead.campaign_name || '—';
  document.getElementById('drawerLeadMetaId').textContent  = lead.fb_lead_id || '—';
  document.getElementById('drawerStatusSelect').value      = lead.status;
  document.getElementById('drawerNotesTextarea').value     = lead.notes || '';

  // Render dynamic form responses
  const answersBox = document.getElementById('drawerFormAnswers');
  const formBadge = document.getElementById('drawerFormNameBadge');
  if (formBadge) formBadge.textContent = lead.form_name || 'Meta Form';

  let fieldItems = [];
  if (lead.raw_data && Array.isArray(lead.raw_data.field_data)) {
    lead.raw_data.field_data.forEach(f => {
      const q = f.name ? f.name.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase()) : 'Question';
      const val = (Array.isArray(f.values) ? f.values.join(', ') : f.values) || '—';
      fieldItems.push({ q, a: val });
    });
  } else if (lead.raw_data && typeof lead.raw_data === 'object') {
    Object.entries(lead.raw_data).forEach(([key, val]) => {
      if (['created_time', 'id', 'form_id', 'page_id', 'ad_id', 'adset_id', 'campaign_id', 'campaign_name', 'field_data'].includes(key)) return;
      const q = key.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
      const displayVal = typeof val === 'object' ? JSON.stringify(val) : String(val);
      fieldItems.push({ q, a: displayVal });
    });
  }

  if (answersBox) {
    if (fieldItems.length === 0) {
      answersBox.innerHTML = '<p style="font-size:12px; color:var(--text-3); font-style:italic;">No custom form fields recorded.</p>';
    } else {
      answersBox.innerHTML = fieldItems.map(item => `
        <div style="background:var(--bg); border:1px solid var(--border); border-radius:8px; padding:10px 12px;">
          <div style="font-size:11px; font-weight:600; color:var(--accent); text-transform:uppercase; letter-spacing:0.04em; margin-bottom:4px;">
            ${esc(item.q)}
          </div>
          <div style="font-size:13px; color:var(--text); font-weight:500; word-break:break-word;">
            ${esc(item.a)}
          </div>
        </div>
      `).join('');
    }
  }

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
    // Optimistic local state update
    const idx = state.leads.findIndex(l => l.id === updated.id);
    if (idx !== -1) state.leads[idx] = updated;
    renderKanban(state.leads);
    renderTable(state.leads, state.totalLeads);
    toast('Status updated');
    loadLeadWorkflow(updated); loadWorkflow();
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
    toast('Notes saved');
    loadLeadActivities(state.activeLead.id);
  } catch (err) { toast(err.message, 'error'); }
});

// WhatsApp chat — DISABLED (WhatsApp integration not configured)
async function loadChatHistory(leadId) {
  // WhatsApp disabled: skip API call
  const box = document.getElementById('whatsappChatBox');
  if (box) box.innerHTML = '<p class="chat-empty">WhatsApp integration not enabled</p>';
}

document.getElementById('whatsappForm').addEventListener('submit', async e => {
  e.preventDefault();
  toast('WhatsApp integration is not enabled yet.', 'info');
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
document.getElementById('formFilterSelect').addEventListener('change', () => {
  state.currentPage = 0;
  loadLeads();
});
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
    toast(`Lead "${lead.name}" created in pipeline`);
    loadLeads();
  } catch (err) { toast(err.message, 'error'); }
});

// ─── Campaigns ───────────────────────────────────────────────────

// Map Meta effective_status to a human-readable label and CSS class
function metaStatusInfo(campaign) {
  const raw = (campaign.meta_status || '').toUpperCase();
  const map = {
    'ACTIVE':               { label: 'Active',      css: 'active' },
    'PAUSED':               { label: 'Paused',      css: 'paused' },
    'DELETED':              { label: 'Deleted',     css: 'deleted' },
    'ARCHIVED':             { label: 'Archived',    css: 'archived' },
    'IN_PROCESS':           { label: 'In Process',  css: 'in_process' },
    'WITH_ISSUES':          { label: 'With Issues', css: 'paused' },
    'CAMPAIGN_PAUSED':      { label: 'Campaign Paused', css: 'paused' },
    'ADSET_PAUSED':         { label: 'Adset Paused',    css: 'paused' },
    'DISAPPROVED':          { label: 'Disapproved',     css: 'deleted' },
    'PENDING_REVIEW':       { label: 'Pending Review',  css: 'in_process' },
    'PENDING_BILLING_INFO': { label: 'Pending Billing', css: 'in_process' },
  };
  if (raw && map[raw]) return map[raw];
  if (raw) return { label: raw.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase()), css: 'unknown' };
  // Fallback to is_active boolean when meta_status not yet synced
  return campaign.is_active
    ? { label: 'Active (local)', css: 'active' }
    : { label: 'Inactive (local)', css: 'inactive' };
}

async function loadCampaigns() {
  if (!state.token) return;
  try {
    state.campaigns = await api('/campaigns') || [];
    updateCampaignMetrics();
    renderCampaignsGrid();
    loadStats();
  } catch (err) { }
}

function updateCampaignMetrics() {
  const total   = state.campaigns.length;
  const active  = state.campaigns.filter(c => (c.meta_status || '').toUpperCase() === 'ACTIVE' || (!c.meta_status && c.is_active)).length;
  const leads   = state.campaigns.reduce((sum, c) => sum + (Number(c.lead_count) || 0), 0);
  const sheets  = state.campaigns.reduce((sum, c) => sum + (c.google_sheets || []).length, 0);

  document.getElementById('campStatTotal')  .textContent = total;
  document.getElementById('campStatActive') .textContent = active;
  document.getElementById('campStatLeads')  .textContent = leads;
  document.getElementById('campStatSheets') .textContent = sheets;
}

window.toggleShowArchivedCampaigns = function() {
  state.hideInactiveCampaigns = !state.hideInactiveCampaigns;
  const btn = document.getElementById('showArchivedCampaignsBtn');
  if (btn) {
    btn.innerHTML = state.hideInactiveCampaigns
      ? '<i data-lucide="eye"></i> Show all'
      : '<i data-lucide="eye-off"></i> Hide inactive';
    lucide.createIcons();
  }
  renderCampaignsGrid();
};

window.refreshCampaignStatuses = async function() {
  if (!state.token) return;
  const btn = document.getElementById('refreshCampaignStatusBtn');
  if (btn) btn.disabled = true;
  try {
    const res = await api('/campaigns/sync-status', { method: 'POST' });
    toast(`Campaigns refreshed from Meta: ${res.updated ?? 0} changed of ${res.checked ?? 0} checked`, 'success');
  } catch (err) {
    toast(err.message || 'Failed to refresh campaign statuses', 'error');
  } finally {
    if (btn) btn.disabled = false;
    loadCampaigns();
  }
};

function renderCampaignsGrid() {
  const grid = document.getElementById('campaignsGrid');
  if (!grid) return;

  // Default: show all campaigns. When toggle is on, hide inactive ones.
  const visible = state.hideInactiveCampaigns
    ? state.campaigns.filter(c => {
        const st = (c.meta_status || '').toUpperCase();
        return st === 'ACTIVE' || (!st && c.is_active);
      })
    : state.campaigns;

  if (!visible.length) {
    grid.innerHTML = `<div class="empty-state">
      <i data-lucide="megaphone"></i>
      <p>${state.hideInactiveCampaigns ? 'No active campaigns right now.<br>Click "Show all" to see all campaigns, or use "Refresh from Meta" to sync status.' : 'No campaigns yet.<br>Create one or connect a Meta page to auto-discover campaigns.'}</p>
    </div>`;
    lucide.createIcons();
    return;
  }
  grid.innerHTML = visible.map(c => {
    const status = metaStatusInfo(c);
    const sheetsList = (c.google_sheets || []).map(s => `
      <div class="campaign-sheet-item">
        <div class="campaign-sheet-name">
          <i data-lucide="file-spreadsheet"></i>
          <span>${esc(s.sheet_name)}</span>
        </div>
        <a class="campaign-sheet-link" href="${esc(s.spreadsheet_url)}" target="_blank" rel="noopener">View</a>
      </div>
    `).join('') || '<div class="campaign-sheet-empty">No Google Sheets connected</div>';

    return `
    <div class="campaign-card">
      <div class="campaign-card-header">
        <h4>${esc(c.name)}</h4>
        <span class="campaign-status-badge ${status.css}">${status.label}</span>
      </div>
      ${c.description ? `<div class="campaign-card-description">${esc(c.description)}</div>` : ''}
      <div class="campaign-card-meta">
        ${c.meta_campaign_id ? `<div class="campaign-meta-row"><i data-lucide="target"></i><span>Campaign ID:</span><code>${esc(c.meta_campaign_id)}</code></div>` : ''}
        ${c.meta_form_id ? `<div class="campaign-meta-row"><i data-lucide="form-input"></i><span>Form ID:</span><code>${esc(c.meta_form_id)}</code></div>` : ''}
        ${c.meta_ad_account_id ? `<div class="campaign-meta-row"><i data-lucide="building-2"></i><span>Ad Account:</span><code>${esc(c.meta_ad_account_id)}</code></div>` : ''}
      </div>
      <div class="campaign-card-sheets">
        <div class="campaign-sheets-header">
          <i data-lucide="file-spreadsheet"></i> Google Sheets (${(c.google_sheets || []).length})
        </div>
        ${sheetsList}
      </div>
      <div class="campaign-card-footer">
        <div class="campaign-footer-left">
          <div class="campaign-assignee">
            <i data-lucide="user"></i>
            <span>${esc(c.assigned_user_name || 'Unassigned')}</span>
          </div>
          <span class="campaign-lead-count">
            <i data-lucide="users"></i>${c.lead_count} leads
          </span>
        </div>
        <div class="campaign-actions">
          <button class="btn btn-ghost" onclick="toggleCampaignArchive(${c.id}, ${c.is_active})">${c.is_active ? 'Archive' : 'Restore'}</button>
          <button class="btn-icon-sm sheet" onclick="openCampaignSheetModal(${c.id})" title="Connect Google Sheet">
            <i data-lucide="file-spreadsheet"></i>
          </button>
          <button class="btn-icon-sm edit" onclick="editCampaign(${c.id})" title="Edit campaign">
            <i data-lucide="pencil"></i>
          </button>
        </div>
      </div>
    </div>`;
  }).join('');
  lucide.createIcons();
}

window.openCampaignSheetModal = function(campaignId) {
  if (!state.token) return;
  populateCampaignDropdown('gsCampaignSelect', campaignId);
  openModal('googleSheetsModal');
};

async function editCampaign(id) {
  const campaign = state.campaigns.find(c => c.id === id);
  if (!campaign) return;
  await populateStaffDropdown('campAssignedUser', campaign.assigned_user_id);
  document.getElementById('campName').value        = campaign.name;
  document.getElementById('campDesc').value        = campaign.description || '';
  document.getElementById('campFormId').value      = campaign.meta_form_id || '';
  document.getElementById('campAdAccountId').value = campaign.meta_ad_account_id || '';

  document.getElementById('createCampaignForm').dataset.editId = id;
  document.getElementById('createCampaignTitle').textContent = 'Edit Campaign';
  document.querySelector('#createCampaignForm button[type="submit"] span, #createCampaignForm button[type="submit"] i')?.closest('button[type="submit"]')?.querySelectorAll('span').forEach(s => { if (s.textContent.includes('Create')) s.textContent = 'Update Campaign'; });
  openModal('createCampaignModal');
}

document.getElementById('createCampaignForm').addEventListener('submit', async e => {
  e.preventDefault();
  const editId = e.target.dataset.editId;
  const btn = e.target.querySelector('button[type="submit"]');
  if (btn) { btn.disabled = true; btn.innerHTML = '<i data-lucide="loader" class="spin"></i> Saving...'; lucide.createIcons(); }
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
      toast('Campaign updated');
    } else {
      await api('/campaigns', { method:'POST', body: JSON.stringify(payload) });
      toast('Campaign created');
    }
    delete e.target.dataset.editId;
    e.target.reset();
    document.getElementById('createCampaignTitle').textContent = 'New Campaign';
    if (btn) {
      const span = btn.querySelector('span');
      if (span) span.textContent = 'Create Campaign';
    }
    closeModal('createCampaignModal');
    loadCampaigns();
  } catch (err) { toast(err.message, 'error'); }
  finally { if (btn) { btn.disabled = false; lucide.createIcons(); } }
});

// ─── Staff ───────────────────────────────────────────────────────
async function loadStaff() {
  if (!state.token) return;
  try {
    state.staff = await api('/staff') || [];
    renderStaffTable();
  } catch (err) { }
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
    toast('Staff member activated');
    loadStaff();
  } catch (err) { toast(err.message, 'error'); }
}

document.getElementById('inviteStaffForm').addEventListener('submit', async e => {
  e.preventDefault();
  const btn = e.target.querySelector('button[type="submit"]');
  if (btn) { btn.disabled = true; btn.innerHTML = '<i data-lucide="loader" class="spin"></i> Sending...'; lucide.createIcons(); }
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
    toast(`${data.name} invited successfully`);
    e.target.reset();
    closeModal('inviteStaffModal');
    loadStaff();
    loadCampaigns();
  } catch (err) { toast(err.message, 'error'); }
  finally { if (btn) { btn.disabled = false; btn.innerHTML = '<i data-lucide="user-plus"></i> Send Invite'; lucide.createIcons(); } }
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
              <h3 style="display:flex; align-items:center; gap:8px;">Facebook Lead Ads <span class="pill active" style="font-size:10px;">Connected</span></h3>
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
    } catch(e) { }
    
    // 2. Fetch Google Sheets Connections
    try {
      const gsConns = await api('/integrations/google-sheets/connections') || [];
      const gsConnectCard = document.getElementById('googleSheetsConnectCard');
      if (gsConnectCard) {
        gsConnectCard.style.display = gsConns.length > 0 ? 'none' : 'flex';
      }

      gsConns.forEach(sc => {
        html += `
          <div class="integration-card" style="margin-top:12px; border:1px solid var(--border);">
            <div class="integration-card-left">
              <div class="integration-icon"><i data-lucide="file-spreadsheet" style="color: #0F9D58;"></i></div>
              <div class="integration-info">
                <h3>${esc(sc.campaign_name)} <span class="integration-status">Tab: ${esc(sc.sheet_name)}</span></h3>
                <p>Syncing to <a href="${esc(sc.spreadsheet_url)}" target="_blank" style="color:var(--accent);text-decoration:underline;">Google Spreadsheet</a></p>
              </div>
            </div>
            <button class="btn btn-ghost" style="color:var(--red); border-color:var(--border);" onclick="deleteGoogleSheetConn(${sc.id})">
              <i data-lucide="trash-2"></i> Remove
            </button>
          </div>
        `;
      });
    } catch (e) { }

    if (html) {
      list.innerHTML = html;
      lucide.createIcons();
    }

    const dot = document.querySelector('.badge-dot');
    const statusText = document.getElementById('connectionStatus');
    if (dot) { dot.className = `badge-dot ${isMetaConnected ? 'connected' : 'disconnected'}`; }
    if (statusText) statusText.textContent = isMetaConnected ? 'Connected' : 'Not Connected';

  } catch (err) { }
}

window.deleteGoogleSheetConn = async function(connId) {
  if (!confirm('Remove this Google Sheet connection? Leads will no longer sync to this worksheet.')) return;
  try {
    await api(`/integrations/google-sheets/connections/${connId}`, { method: 'DELETE' });
    toast('Google Sheet connection removed');
    loadIntegrations();
    loadCampaigns();
  } catch (err) {
    toast('Failed to remove connection: ' + err.message, 'error');
  }
};

document.getElementById('btnConnectGoogleSheets')?.addEventListener('click', () => {
  if (!state.token) return;
  populateCampaignDropdown('gsCampaignSelect');
  openModal('googleSheetsModal');
});

document.getElementById('closeGoogleSheetsModal')?.addEventListener('click', () => closeModal('googleSheetsModal'));
document.getElementById('cancelGoogleSheets')?.addEventListener('click', () => closeModal('googleSheetsModal'));

document.getElementById('googleSheetsForm')?.addEventListener('submit', async (e) => {
  e.preventDefault();
  const btn = e.target.querySelector('button[type="submit"]');
  if (btn) { btn.disabled = true; btn.innerHTML = '<i data-lucide="loader" class="spin"></i> Connecting...'; lucide.createIcons(); }
  const campVal = document.getElementById('gsCampaignSelect').value;
  const payload = {
    spreadsheet_url: document.getElementById('gsUrl').value,
    sheet_name: document.getElementById('gsSheetName').value || 'Sheet1',
    campaign_id: campVal ? parseInt(campVal, 10) : null
  };
  try {
    await api('/integrations/google-sheets/connect', { method: 'POST', body: JSON.stringify(payload) });
    toast('Google Sheet Connected! Leads will now sync automatically.');
    closeModal('googleSheetsModal');
    loadIntegrations();
    loadCampaigns();
  } catch (err) { toast(err.message, 'error'); }
  finally { if (btn) { btn.disabled = false; btn.innerHTML = '<i data-lucide="file-spreadsheet"></i> Connect Sheet'; lucide.createIcons(); } }
});

// ─── Charts ──────────────────────────────────────────────────────
document.getElementById('analyticsDateRange')?.addEventListener('change', renderCharts);

function getChartColors() {
  const isDark = document.documentElement.getAttribute('data-theme') === 'dark';
  return {
    borderColor: isDark ? '#1e2433' : '#ffffff',
    legendColor: isDark ? '#8b95ad' : '#4b5568',
    axisColor: isDark ? '#8b95ad' : '#4b5568',
    gridColor: isDark ? 'rgba(255,255,255,0.05)' : 'rgba(0,0,0,0.06)',
    pointBg: isDark ? '#1e2433' : '#ffffff',
    tooltipBg: isDark ? 'rgba(15, 23, 42, 0.9)' : 'rgba(255, 255, 255, 0.95)',
    tooltipText: isDark ? '#f0f4ff' : '#111827',
  };
}

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
  const cc = getChartColors();
  state.statusChart = new Chart(document.getElementById('statusChart'), {
    type: 'doughnut',
    data: {
      labels: Object.keys(statusCounts).map(s => s.charAt(0).toUpperCase() + s.slice(1)),
      datasets: [{ 
        data: Object.values(statusCounts), 
        backgroundColor: Object.values(colors), 
        borderWidth: 2,
        borderColor: cc.borderColor,
        hoverOffset: 4
      }],
    },
    options: { 
      responsive: true, 
      cutout: '75%',
      plugins: { 
        legend: { position:'bottom', labels: { color: cc.legendColor, font: { size: 13, family: 'Inter' }, padding: 20 } } 
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
        tension: 0.4,
        fill: true,
        pointBackgroundColor: cc.pointBg,
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
        x: { ticks: { color: cc.axisColor, font: { family: 'Inter' } }, grid: { display: false } },
        y: { beginAtZero: true, ticks: { color: cc.axisColor, font: { family: 'Inter' }, stepSize: 1 }, grid: { color: cc.gridColor, borderDash: [5, 5] } },
      },
      plugins: { 
        legend: { display: false },
        tooltip: {
          backgroundColor: cc.tooltipBg,
          titleFont: { size: 13, family: 'Inter' },
          bodyFont: { size: 14, family: 'Inter', weight: 'bold' },
          titleColor: cc.tooltipText,
          bodyColor: cc.tooltipText,
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
  return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
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
  navigator.clipboard.writeText(el.textContent).then(() => toast('Copied to clipboard'));
}

// ─── Initialise ───────────────────────────────────────────────────
async function loadAll() {
  updateUserUI();
  if (state.token) {
    state.dateFrom = null;
    state.dateTo   = null;
    state.activeChip = 'all';
    ['chipToday','chipYesterday','chipWeek','chipAll'].forEach(id => {
      document.getElementById(id)?.classList.remove('active');
    });
    document.getElementById('chipAll')?.classList.add('active');
    await Promise.all([loadCampaigns(), loadLeads(), loadIntegrations(), loadWorkflow()]);
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
    toast('Facebook Page successfully connected!', 'success');
    tempFbSession = null;
    loadIntegrations();
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
        loadIntegrations();
      } catch (e) {
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

async function syncMetaLeads() {
  if (!state.token) { openModal('authModal'); return; }
  const btn = document.getElementById('syncMetaBtn');
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = `<i data-lucide="refresh-cw" class="spin"></i><span>Syncing...</span>`;
    lucide.createIcons();
  }
  try {
    const res = await api('/integrations/facebook/sync', { method: 'POST' });
    if (res && res.sync_results) {
      const { leads_imported, duplicates_skipped } = res.sync_results;
      toast(`Sync complete! ${leads_imported} new lead(s) imported, ${duplicates_skipped} duplicate(s) skipped.`, 'success');
    } else {
      toast(res?.message || 'Sync complete!', 'success');
    }
    loadLeads();
    loadStats();
  } catch (err) {
    toast('Meta sync failed: ' + err.message, 'error');
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = `<i data-lucide="refresh-cw"></i><span>Sync Meta</span>`;
      lucide.createIcons();
    }
  }
}

// Boot
document.addEventListener('DOMContentLoaded', () => {
  lucide.createIcons();
  
  // Sync Meta Leads listener
  document.getElementById('syncMetaBtn')?.addEventListener('click', syncMetaLeads);
  
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
    function setSidebarOpen(open) {
      sidebar.classList.toggle('open', open);
      sidebarBackdrop.classList.toggle('active', open);
      sidebarToggleBtn.setAttribute('aria-expanded', String(open));
      sidebarToggleBtn.setAttribute('aria-label', open ? 'Close navigation' : 'Open navigation');
      sidebar.inert = window.innerWidth <= 900 && !open;
      if (open) sidebar.querySelector('.nav-item')?.focus();
    }
    function toggleSidebar() {
      setSidebarOpen(!sidebar.classList.contains('open'));
    }
    sidebar.querySelectorAll('.nav-item').forEach(item => {
      item.tabIndex = 0;
      item.setAttribute('role', 'button');
      item.addEventListener('keydown', event => {
        if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); item.click(); }
      });
    });
    setSidebarOpen(false);
    sidebarToggleBtn.addEventListener('click', toggleSidebar);
    sidebarBackdrop.addEventListener('click', () => { setSidebarOpen(false); sidebarToggleBtn.focus(); });
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape' && sidebar.classList.contains('open')) { setSidebarOpen(false); sidebarToggleBtn.focus(); }
    });
    window.matchMedia('(max-width: 900px)').addEventListener('change', () => setSidebarOpen(false));
    
    // Close sidebar when clicking a nav item on mobile
    const navItems = sidebar.querySelectorAll('.nav-item');
    navItems.forEach(item => {
      item.addEventListener('click', () => {
        if (window.innerWidth <= 900) {
          setSidebarOpen(false);
          sidebarToggleBtn.focus();
        }
      });
    });
  }

  // Filter change listeners — only register once here, removed top-level duplicates

  loadAll();

  // Handle OAuth callback success message
  const urlParams = new URLSearchParams(window.location.search);
  if (urlParams.get('meta_connected') === 'true') {
    const pagesCount = urlParams.get('pages') || '1';
    setTimeout(() => {
      toast(`Successfully connected ${pagesCount} Facebook Page(s)!`);
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
