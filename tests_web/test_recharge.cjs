const {test} = require("node:test");
const assert = require("node:assert/strict");
const {calculateRecharge} = require("../src/static/dashboard.js");
test("purchase cost ignores balance; total duration includes existing electricity", () => {
 for (const balance of [0,449,-20,10000]) {
  const plan=calculateRecharge({balance,average:7.25,price:0.5,days:30});
  assert.equal(plan.amount,108.75);
  assert.ok(Math.abs(plan.days-Math.max(0,balance+217.5)/7.25)<1e-10);
 }
});
test("total days uses converted electricity rather than adding yuan to kWh", () => {
 assert.deepEqual(calculateRecharge({balance:100,average:10,price:0.8,days:30}),{amount:240,days:40});
});
test("currency rounds to cents, and total duration uses that amount", () => {
 const plan=calculateRecharge({balance:100,average:7.123,price:0.5,days:30});
 assert.equal(plan.amount,106.85);
 assert.ok(Math.abs(plan.days-(100+106.85/0.5)/7.123)<1e-10);
 assert.equal(calculateRecharge({balance:0,average:7,price:0.1+0.2,days:100}).amount,210);
});
test("invalid balance and inputs do not produce advice", () => {
 const base={balance:100,average:10,price:0.5,days:30};
 for(const change of [{balance:null},{balance:Infinity},{balance:undefined},{average:null},{average:0},{average:Infinity},{price:0},{price:-1},{price:NaN},{price:101},{days:0},{days:30.5},{days:366},{days:NaN}])
 assert.equal(calculateRecharge({...base,...change}),null);
});
