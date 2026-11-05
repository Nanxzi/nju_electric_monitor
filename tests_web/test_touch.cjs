const {test} = require('node:test');
const assert = require('node:assert/strict');
const {bindChartPointerInteractions} = require('../src/static/dashboard.js');
function chartHarness() {
  const handlers = new Map();
  const selected = [];
  let visible = false;
  const captures = [];
  const chart = {addEventListener: (name, handler) => handlers.set(name, handler),
    setPointerCapture: id => captures.push(id)};
  bindChartPointerInteractions(chart, event => {selected.push(event.clientX); visible = true;}, () => {visible = false;});
  return {emit(name, values = {}) {handlers.get(name)({isPrimary:true, button:0, pointerType:'touch', pointerId:1, clientX:80, ...values});},
    selected, captures, visible: () => visible};
}
test('a stationary touch selects immediately and remains visible after lifting', () => {
  const h = chartHarness();
  h.emit('pointerdown'); h.emit('pointerup'); h.emit('lostpointercapture'); h.emit('pointerleave');
  assert.deepEqual(h.selected, [80,80]); assert.equal(h.visible(), true);
});
test('touch drag tracks the captured finger and ignores a second finger', () => {
  const h = chartHarness(); h.emit('pointerdown');
  h.emit('pointerdown', {isPrimary:false, pointerId:2});
  h.emit('pointermove', {clientX:140}); h.emit('pointermove', {pointerId:2, clientX:200});
  h.emit('pointerup', {clientX:160});
  assert.deepEqual(h.selected, [80,140,160]); assert.deepEqual(h.captures, [1]);
});
test('browser cancellation for vertical scrolling hides and stops touch tracking', () => {
  const h = chartHarness(); h.emit('pointerdown'); h.emit('pointercancel');
  h.emit('pointermove', {clientX:200});
  assert.equal(h.visible(),false); assert.deepEqual(h.selected,[80]);
});
test('mouse hover continues to show and dismiss readings', () => {
  const h = chartHarness(); h.emit('pointermove', {pointerType:'mouse'});
  assert.equal(h.visible(),true); h.emit('pointerleave', {pointerType:'mouse'});
  assert.equal(h.visible(),false);
});
