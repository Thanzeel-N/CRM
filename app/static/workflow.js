let followUpOffset = 0;
let workflowRequest = 0;
let previousOverdue = null;
function resetWorkflow() {
  workflowRequest++; followUpOffset = 0; previousOverdue = null;
  for (const id of ['workflowSummary', 'workflowAnalytics', 'followUpPages', 'integrationJobs', 'leadActivities']) document.getElementById(id).textContent = '';
  document.getElementById('followUpList').textContent = 'Sign in to see your follow-ups.';
}


async function loadWorkflow() {
  if (!state.token) return;
  const request = ++workflowRequest;

  try {
    const [queue, metrics] = await Promise.all([
      api(`/workflow/follow-ups?offset=${followUpOffset}`),
      api('/workflow/analytics'),
    ]);
    if (!queue || !metrics || request !== workflowRequest || !state.token) return;
    if (previousOverdue !== null && metrics.overdue > previousOverdue) toast(`${metrics.overdue} follow-ups are overdue. Open Today to review them.`, 'info');
    previousOverdue = metrics.overdue;
    document.getElementById('workflowSummary').innerHTML = `
      <div class="workflow-panel"><span>Due by tonight</span><strong>${queue.total}</strong></div>
      <div class="workflow-panel"><span>Overdue now</span><strong>${metrics.overdue}</strong></div>
      <div class="workflow-panel"><span>Average first response</span><strong>${metrics.average_response_hours === null ? 'No data yet' : metrics.average_response_hours + ' hours'}</strong><small>${metrics.response_samples} recorded contacts</small></div>`;
    document.getElementById('followUpList').innerHTML = queue.items.length ? queue.items.map(l => {
      const due = utcDate(l.follow_up_at);
      return `<article class="follow-up-card"><div><span class="pill ${due < new Date() ? 'lost' : 'contacted'}">${due < new Date() ? 'Overdue' : 'Today'}</span><h3>${esc(l.name || 'Unnamed lead')}</h3><p>${esc(l.campaign_name || 'No campaign')} · ${esc(l.owner_name || 'Campaign assignment')}</p><time>${esc(regionFormat(due))}</time></div><button class="btn btn-primary" onclick="openWorkflowLead(${l.id})">Open lead</button></article>`;
    }).join('') : '<div class="workflow-panel"><h3>You’re up to date</h3><p>No open follow-ups due by tonight. Schedule a next action from any lead’s details.</p></div>';
    document.getElementById('followUpPages').innerHTML = `<button class="btn btn-ghost" ${followUpOffset === 0 ? 'disabled' : ''} onclick="pageFollowUps(-50)">Previous</button><span>${queue.total ? followUpOffset + 1 : 0}–${Math.min(followUpOffset + 50, queue.total)} of ${queue.total}</span><button class="btn btn-ghost" ${followUpOffset + 50 >= queue.total ? 'disabled' : ''} onclick="pageFollowUps(50)">Next</button>`;
    const table = (title, rows) => `<h4>${title}</h4><div class="table-wrap"><table class="data-table"><thead><tr><th>Name</th><th>Leads</th><th>Converted</th><th>Conversion</th></tr></thead><tbody>${rows.map(r => `<tr><td>${esc(r.name)}</td><td>${r.total}</td><td>${r.converted}</td><td>${r.conversion_rate}%</td></tr>`).join('') || '<tr><td colspan="4">No leads yet</td></tr>'}</tbody></table></div>`;
    document.getElementById('workflowAnalytics').innerHTML = table('Campaigns', metrics.campaigns) + table('Current lead owners', metrics.agents);
  } catch (err) {
    document.getElementById('followUpList').textContent = 'Unable to load follow-ups. Use Refresh to try again.';
    toast(err.message, 'error');
  }
}
function pageFollowUps(delta) { followUpOffset = Math.max(0, followUpOffset + delta); loadWorkflow(); }
async function openWorkflowLead(id) {
  try { const lead = await api(`/leads/${id}`); if (lead) openLeadDrawer(lead); }
  catch (err) { toast(err.message, 'error'); }
}
async function loadLeadActivities(id) {
  const box = document.getElementById('leadActivities');
  box.textContent = 'Loading history…';
  try {
    const rows = await api(`/workflow/leads/${id}/activities`);
    if (state.activeLead?.id !== id || !rows) return;
    box.innerHTML = rows.map(a => `<article><strong>${esc(a.actor_name)} · ${esc(a.kind.replaceAll('_', ' '))}</strong><p>${esc(a.detail)}</p><time>${esc(regionFormat(a.created_at))}</time></article>`).join('') || '<p class="hint">No recorded activity yet.</p>';
  } catch (err) { if (state.activeLead?.id === id) box.textContent = 'Could not load history.'; }
}
async function loadLeadWorkflow(lead) {
  const input = document.getElementById('followUpAt');
  const due = utcDate(lead.follow_up_at);
  input.value = due ? regionInput(due) : '';
  document.getElementById('activityDetail').value = '';
  const select = document.getElementById('leadOwner');
  select.innerHTML = `<option value="${lead.owner_id || ''}">${esc(lead.owner_name || 'Campaign assignment')}</option>`;
  select.disabled = true;
  const call = document.getElementById('callLead');
  const phone = (lead.phone || '').replace(/[^+\d]/g, '');
  call.hidden = !phone; call.href = 'tel:' + phone;
  loadLeadActivities(lead.id);
  if (state.user?.role === 'admin') {
    try {
      const owners = await api('/workflow/owners');
      if (state.activeLead?.id !== lead.id || !owners) return;
      select.innerHTML = '<option value="">Campaign assignment</option>' + owners.map(o => `<option value="${o.id}">${esc(o.name)}</option>`).join('');
      if (lead.owner_id && !owners.some(o => o.id === lead.owner_id)) select.add(new Option(lead.owner_name + ' (inactive)', lead.owner_id));
      select.value = lead.owner_id || ''; select.disabled = false;
    } catch (err) { toast(err.message, 'error'); }
  }
}
document.getElementById('saveFollowUp').addEventListener('click', async function() {
  const lead = state.activeLead; if (!lead) return;
  this.disabled = true;
  try {
    const value = document.getElementById('followUpAt').value;
    const payload = {follow_up_local: value || null};
    const owner = document.getElementById('leadOwner');
    if (state.user?.role === 'admin' && !owner.disabled && (Number(owner.value) || null) !== lead.owner_id) payload.owner_id = Number(owner.value) || null;
    const updated = await api(`/workflow/leads/${lead.id}`, {method: 'PATCH', body: JSON.stringify(payload)});
    if (!updated) return;
    if (state.activeLead?.id === lead.id) { state.activeLead = updated; loadLeadWorkflow(updated); }
    followUpOffset = 0; loadWorkflow(); loadLeads(); toast('Next action saved');
  } catch (err) { toast(err.message, 'error'); }
  finally { this.disabled = false; }
});
async function recordLeadActivity(kind, detail, button) {
  const lead = state.activeLead; if (!lead) return;
  button.disabled = true;
  try {
    const result = await api(`/workflow/leads/${lead.id}/activities`, {method: 'POST', body: JSON.stringify({kind, detail})});
    if (!result) return;
    if (state.activeLead?.id === lead.id) await openWorkflowLead(lead.id);
    followUpOffset = 0; loadWorkflow(); loadLeads(); toast('Activity saved');
  } catch (err) { toast(err.message, 'error'); }
  finally { button.disabled = false; }
}
document.getElementById('completeFollowUp').addEventListener('click', function() { recordLeadActivity('follow_up_completed', 'Follow-up completed', this); });
document.getElementById('addActivity').addEventListener('click', function() {
  const detail = document.getElementById('activityDetail').value.trim();
  if (!detail) { toast('Enter the activity details', 'error'); return; }
  recordLeadActivity(document.getElementById('activityKind').value, detail, this);
});
async function toggleCampaignArchive(id, active) {
  try {
    const result = await api(`/campaigns/${id}`, active ? {method: 'DELETE'} : {method: 'PATCH', body: JSON.stringify({is_active: true})});
    if (!result) return;
    await loadCampaigns(); toast(active ? 'Campaign archived; leads preserved' : 'Campaign restored');
  } catch (err) { toast(err.message, 'error'); }
}
async function loadIntegrationJobs() {
  if (state.user?.role !== 'admin') return;
  const box = document.getElementById('integrationJobs');
  try {
    const rows = await api('/workflow/integration-jobs'); if (!rows) return;
    box.innerHTML = rows.map(j => `<article class="follow-up-card"><div><strong>${esc(j.kind === 'meta' ? 'Meta lead capture' : 'Google Sheets')} · ${esc(j.status)}</strong><p>${esc(j.last_error || 'Waiting for processing')} · ${j.attempts} attempts</p></div>${['pending','failed'].includes(j.status) ? `<button class="btn btn-secondary" onclick="retryIntegrationJob(${j.id}, this)">Retry now</button>` : ''}</article>`).join('') || '<p class="hint">No pending or failed deliveries.</p>';
  } catch (err) { box.textContent = 'Unable to load sync activity.'; }
}
async function retryIntegrationJob(id, button) {
  button.disabled = true;
  try { await api(`/workflow/integration-jobs/${id}/retry`, {method:'POST'}); await loadIntegrationJobs(); }
  catch (err) { toast(err.message, 'error'); button.disabled = false; }
}
setInterval(() => { if (state.token && !document.hidden) { loadWorkflow(); if (document.getElementById('tab-connect').classList.contains('active')) loadIntegrationJobs(); } }, 60000);
