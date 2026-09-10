const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');

function setup() {
  const elements = new Map();
  const document = {hidden: false, getElementById(id) {
    if (!elements.has(id)) elements.set(id, {value: '', textContent: '', innerHTML: '', disabled: false, handlers: {}, classList: {contains: () => false}, addEventListener(event, callback) { this.handlers[event] = callback; }});
    return elements.get(id);
  }};
  const calls = [];
  const context = vm.createContext({document, Date, console, setInterval() {}, state: {token: 'test', user: {role: 'agent'}, activeLead: {id: 1, owner_id: 2}}, toast() {}, esc: s => String(s).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;'), loadLeads() {}, openLeadDrawer() {}, api: async (path, options) => {
    calls.push({path, options});
    if (path.startsWith('/workflow/follow-ups')) return {total: 1, items: [{id: 1, name: '<img onerror=evil()>', follow_up_at: '2026-01-01T12:00:00', campaign_name: 'Test', owner_name: 'Agent'}]};
    if (path === '/workflow/analytics') return {overdue: 1, average_response_hours: null, response_samples: 0, campaigns: [], agents: []};
    return {id: 1, follow_up_at: null};
  }});
  vm.runInContext(fs.readFileSync('app/static/workflow.js', 'utf8'), context);
  return {context, document, calls};
}

test('dashboard escapes lead content and handles UTC dates without offsets', async () => {
  const {context, document} = setup();
  await context.loadWorkflow();
  assert.match(document.getElementById('followUpList').innerHTML, /&lt;img/);
  assert.doesNotMatch(document.getElementById('followUpList').innerHTML, /<img/);
  assert.equal(context.utcDate('2026-09-10T04:30:00').toISOString(), '2026-09-10T04:30:00.000Z');
  assert.equal(context.utcDate('2026-09-10T10:00:00+05:30').toISOString(), '2026-09-10T04:30:00.000Z');
});

test('agent can save a follow-up without submitting an owner change', async () => {
  const {context, document, calls} = setup();
  context.loadLeadWorkflow = () => {};
  document.getElementById('followUpAt').value = '2026-09-10T10:00';
  const button = document.getElementById('saveFollowUp');
  await button.handlers.click.call(button);
  const request = calls.find(c => c.options?.method === 'PATCH');
  const payload = JSON.parse(request.options.body);
  assert.equal(request.path, '/workflow/leads/1');
  assert.equal(payload.follow_up_at, new Date('2026-09-10T10:00').toISOString());
  assert.equal('owner_id' in payload, false);
  assert.equal(button.disabled, false);
});

test('logout invalidates pending dashboard results', async () => {
  const {context, document} = setup();
  const pending = context.loadWorkflow();
  context.resetWorkflow();
  await pending;
  assert.equal(document.getElementById('followUpList').textContent, 'Sign in to see your follow-ups.');
  assert.equal(document.getElementById('workflowSummary').innerHTML, '');
});
