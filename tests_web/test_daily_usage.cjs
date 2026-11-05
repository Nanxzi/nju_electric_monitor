const {test}=require('node:test');
const assert=require('node:assert/strict');
const {dailyConsumption}=require('../src/static/dashboard.js');
const rows=(...items)=>items.map(([time,balance])=>({timestamp:Date.parse(time+'+08:00'),balance}));
test('Beijing midnight splits consumption proportionally and conserves total',()=>{
 const r=dailyConsumption(rows(['2026-10-01T12:00:00',100],['2026-10-02T12:00:00',88]));
 assert.deepEqual(r.map(x=>x.balance),[6,6]);
 assert.match(r[0].display_time,/2026-10-01.*未覆盖全天/);
});
test('daily points aggregate intervals, full days and zero consumption',()=>{
 const r=dailyConsumption(rows(['2026-10-01T00:00:00',100],['2026-10-01T12:00:00',95],['2026-10-02T00:00:00',90],['2026-10-03T00:00:00',90]));
 assert.deepEqual(r.map(x=>x.balance),[10,0]);
 assert.equal(r[0].display_time,'2026-10-01');
});
test('recharge and long gaps are omitted, not shown as zero consumption',()=>{
 assert.deepEqual(dailyConsumption(rows(['2026-10-01T00:00:00',10],['2026-10-02T00:00:00',100])),[]);
 assert.deepEqual(dailyConsumption(rows(['2026-10-01T00:00:00',100],['2026-10-05T00:00:00',50])),[]);
 assert.deepEqual(dailyConsumption([]),[]);
});

test("both charts render, support touch and share custom date ranges",()=>{
const fs=require('node:fs');
const vm=require('node:vm');
const nodes=new Map(), observers=[];
class Node {
 constructor(){this.children=[];this.handlers={};this.style={};this.hidden=false;this.attrs={};this.classList={toggle(){}};this.validity={valid:true};this.clientWidth=360;this.clientHeight=272;this.offsetWidth=120;this.offsetHeight=50;}
 addEventListener(k,f){(this.handlers[k]??=[]).push(f);}
 emit(k,e={}){for(const f of this.handlers[k]||[])f(e);}
 setAttribute(k,v){this.attrs[k]=v;}
 append(...c){this.children.push(...c);}
 replaceChildren(...c){this.children=c;}
 querySelector(){return null;}
 querySelectorAll(){return [];}
 contains(n){return n===this;}
 getBoundingClientRect(){return {left:0,width:360};}
 setPointerCapture(){}
 focus(){} scrollIntoView(){}
}
const get=id=>{if(!nodes.has(id))nodes.set(id,new Node());return nodes.get(id);};
const buttons=['7','30','90','all'].map(days=>Object.assign(new Node(),{dataset:{days}}));
const records=[{time:'2026-10-01T00:00:00+08:00',balance:100,display_time:'2026-10-01 00:00:00'},
{time:'2026-10-02T00:00:00+08:00',balance:88,display_time:'2026-10-02 00:00:00'},
{time:'2026-10-03T00:00:00+08:00',balance:80,display_time:'2026-10-03 00:00:00'}];
get('dashboard-data').textContent=JSON.stringify({records,balance:80,daily_usage:10});
get('target-days').valueAsNumber=30;get('electricity-price').valueAsNumber=.5;
const document=Object.assign(new Node(),{getElementById:get,querySelectorAll:()=>buttons,createElement:()=>new Node(),createElementNS:()=>new Node(),createTextNode:text=>({textContent:text})});
vm.runInNewContext(fs.readFileSync(require('node:path').join(__dirname, '../src/static/dashboard.js'),'utf8'),{
 document,window:{matchMedia:()=>({matches:true,addEventListener(){}})},ResizeObserver:class{constructor(cb){observers.push(cb);}observe(){}},Intl,Date,console,
});
for(const cb of observers)cb();
assert.match(get('usage-chart').attrs['aria-label'],/每日用电量.*2 条记录/);
assert.match(get('trend-chart').attrs['aria-label'],/剩余电量.*3 条记录/);
get('usage-chart').emit('pointerdown',{isPrimary:true,button:0,pointerType:'touch',pointerId:1,clientX:350});
assert.equal(get('usage-tooltip').hidden,false);
assert.equal(get('usage-tooltip').children[1].textContent,'8.00 kWh');
get('usage-chart').emit('pointerup',{pointerType:'touch',pointerId:1,clientX:350});
get('usage-chart').emit('pointerleave',{pointerType:'touch'});
assert.equal(get('usage-tooltip').hidden,false);
buttons[0].emit('click');
assert.equal(get('usage-tooltip').hidden,true);
get('range-start').value='2026-10-02';get('range-end').value='2026-10-02';
get('date-range-form').emit('submit',{preventDefault(){}});
assert.match(get('usage-chart').attrs['aria-label'],/1 条记录/);
assert.match(get('trend-chart').attrs['aria-label'],/1 条记录/);
console.log('双图渲染、手机读数、切换区间、自定义日期验证通过。');

});
