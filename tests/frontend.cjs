// Newly constructed synthetic fixtures only. No HA, network, photos or backups.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync(path.join(__dirname, '../www/solar-dashboard/sigenergy-flow-card.js'), 'utf8');
const context = {HTMLElement: class {}, customElements: {get: () => true}, window: {}, matchMedia: () => ({matches: false}), Intl, Date};
vm.runInNewContext(source + '\nthis.Card = SigenergyFlowCard;', context);
const proto = context.Card.prototype;
let count = 0;
function check(name, fn) { fn(); count++; console.log('PASS ' + name); }
check('full source has no embedded image or identifying vehicle lookup literals', () => {
  assert.ok(!/data:image\//.test(source));
  assert.ok(!/row\.(?:device_id|config_entry_id)\s*===\s*['"][^'"]+['"]/.test(source));
  assert.ok(source.includes('assets/placeholder.svg'));
});
check('exact decimal money and independent mathematical ceiling', () => {
  for (const [baseline, schedule, value, display] of [
    ['10.001', '2.0001', '8.0009', '£9'],
    ['1.1', '2.3', '-1.2', '−£1'],
    ['1.00001', '1', '0.00001', '£1'],
    ['0', '0.2', '-0.2', '£0']
  ]) {
    const got = proto._financeScheduleSaving({baseline, schedule});
    assert.equal(got.value, value); assert.equal(got.display, display);
  }
  for (const bad of [null, 'NaN', 'Infinity', '1e9999']) assert.equal(proto._financeScheduleSaving({baseline: bad, schedule: '0'}), null);
});
const model = () => ({model_scenario: 'synthetic-example', matched: true, period: 'month', coverage_fingerprint: 'a'.repeat(64), blockers: [], schedule_saving_range: ['1.01', '1.99']});
check('matched ranges retain both independently rounded endpoints', () => {
  const card = Object.create(proto);
  assert.equal(card._ordinaryFinanceScenario(model(), 'month').display, '£2 – £2');
  for (const change of [{matched: false}, {period: 'lifetime'}, {coverage_fingerprint: ''}, {blockers: ['synthetic gap']}, {schedule_saving_range: ['2', '1']}]) {
    assert.equal(card._ordinaryFinanceScenario({...model(), ...change}, 'month'), null);
  }
});
check('six current finance tiles, not an unimplemented fourth column', () => {
  let markup; const stop = new Error('synthetic markup stop');
  try { proto._build.call({shadowRoot: {set innerHTML(v) {markup = v; throw stop;}}, config: {home_entities: []}, _pv: []}); }
  catch (e) { assert.equal(e, stop); }
  assert.equal((markup.match(/class="finance-tile pending"/g) || []).length, 6);
  assert.ok(markup.includes('How much it would have cost before solar was installed'));
  assert.ok(markup.includes('Estimated saving with a timed schedule'));
});
function tile() {
  const fields = {strong: {}, em: {}, small: {textContent: 'Example'}};
  return {fields, classList: {toggle() {}}, querySelector: k => fields[k], setAttribute() {}};
}
const nodes = {financeDetails: {textContent: ''}};
for (const period of ['month', 'lifetime']) for (const column of ['baseline', 'saving', 'schedule']) nodes[`finance_${period}_${column}`] = tile();
const card = Object.assign(Object.create(proto), {config: {}, _id: id => nodes[id], _hass: {states: {}, connection: {connected: true}}});
check('missing backend never fabricates numeric zero', () => {
  card._updateFinance();
  assert.equal(nodes.finance_month_baseline.fields.strong.textContent, 'Collecting data');
});
check('all six tiles use corrected view, with stale/disconnected hiding', () => {
  const base = {...model(), coverage_start: '2026-01-05T12:00:00Z', coverage_end: '2026-01-05T13:00:00Z', covered_hours: '1', values: {baseline: '10', actual: '4', saving: '6'}, display: {baseline: '£10', saving: '£6'}, simulated_initial_stored_range: ['1', '2'], simulated_final_stored_range: ['2', '3'], actual_saving: '6'};
  const report = {solar_finance_schema: 1, last_sample_at: new Date().toISOString(), periods: {month: {display: {baseline: 'WRONG PARENT'}}}, corrected_view: {periods: {month: base, lifetime: {...base, period: 'lifetime'}}}};
  card._hass.states = {'sensor.example_finance': {state: 'Covered period only', attributes: report}};
  card._updateFinance();
  assert.equal(nodes.finance_month_baseline.fields.strong.textContent, '£10');
  assert.equal(nodes.finance_lifetime_schedule.fields.strong.textContent, '£2 – £2');
  assert.ok(nodes.financeDetails.textContent.includes('Same recovered coverage'));
  report.last_sample_at = new Date(Date.now() - 240000).toISOString(); card._updateFinance();
  assert.equal(nodes.finance_month_baseline.fields.strong.textContent, 'Collecting data');
  report.last_sample_at = new Date().toISOString(); card._hass.connection.connected = false; card._updateFinance();
  assert.equal(nodes.finance_month_schedule.fields.strong.textContent, 'Collecting data');
});
check('vehicle registry lookup has no unscoped default match', () => {
  const rows = [{device_id: 'example-device', config_entry_id: 'example-entry', platform: 'vag_connect', entity_id: 'sensor.example_soc', unique_id: 'example_battery_soc'}];
  assert.equal(proto._cupraSocEntity.call({config: {}}, rows), null);
  assert.equal(proto._cupraSocEntity.call({config: {ev_device_id: 'example-device', ev_config_entry_id: 'example-entry'}}, rows), 'sensor.example_soc');
});
console.log(`${count} synthetic frontend checks passed; no browser/device behaviour claimed.`);
