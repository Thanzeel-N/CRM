function crmTimezone() { return 'Asia/Kolkata'; }
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
