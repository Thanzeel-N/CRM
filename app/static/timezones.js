function crmTimezone() { return state.timezone || 'Asia/Kolkata'; }
function utcDate(value) {
  if (!value) return null;
  if (value instanceof Date) return value;
  return new Date(/[zZ]$|[+-]\d\d:\d\d$/.test(value) ? value : value + 'Z');
}
function regionParts(value) {
  const date = utcDate(value);
  if (!date || isNaN(date)) return null;
  return Object.fromEntries(new Intl.DateTimeFormat('en-GB', {
    timeZone: crmTimezone(), year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
  }).formatToParts(date).map(p => [p.type, p.value]));
}
function regionDay(value = new Date()) {
  const p = regionParts(value);
  return p ? `${p.year}-${p.month}-${p.day}` : '';
}
function regionInput(value) {
  const p = regionParts(value);
  return p ? `${p.year}-${p.month}-${p.day}T${p.hour}:${p.minute}` : '';
}
function regionFormat(value, options = {dateStyle: 'medium', timeStyle: 'short'}) {
  const date = utcDate(value);
  return date && !isNaN(date) ? new Intl.DateTimeFormat('en-GB', {...options, timeZone: crmTimezone()}).format(date) : '—';
}
async function loadRegionSettings() {
  const org = await api('/org/settings');
  if (!org) return;
  state.timezone = org.timezone || 'Asia/Kolkata';
  document.querySelectorAll('.timezone-label').forEach(el => { el.textContent = crmTimezone(); });
  const select = document.getElementById('orgTimezone');
  if (state.user?.role === 'admin' && select) {
    const zones = await api('/org/timezones');
    select.replaceChildren(...(zones || [crmTimezone()]).map(zone => new Option(zone.replaceAll('_', ' '), zone)));
    select.value = crmTimezone();
  }
}
document.getElementById('detectTimezone')?.addEventListener('click', () => {
  const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const select = document.getElementById('orgTimezone');
  if (![...select.options].some(option => option.value === zone)) select.add(new Option(zone, zone));
  select.value = zone;
});
document.getElementById('regionSettingsForm')?.addEventListener('submit', async event => {
  event.preventDefault();
  const button = event.target.querySelector('button[type="submit"]');
  button.disabled = true;
  try {
    const org = await api('/org/settings', {method: 'PATCH', body: JSON.stringify({timezone: document.getElementById('orgTimezone').value})});
    if (!org) return;
    await loadRegionSettings();
    if (state.activeChip !== 'all') setLeadDateChip(state.activeChip);
    await Promise.all([loadLeads(), loadWorkflow()]);
    if (state.activeLead) openLeadDrawer(state.activeLead);
    toast(`Timezone saved: ${org.timezone}. Sheet date updates are queued.`);
  } catch (err) { toast(err.message, 'error'); }
  finally { button.disabled = false; }
});
