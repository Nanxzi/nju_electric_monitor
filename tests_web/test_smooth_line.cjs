const {test}=require("node:test");
const assert=require("node:assert/strict");
const {smoothLinePath}=require("../src/static/dashboard.js");
test("smooth curves interpolate every observation and do not overshoot",()=>{
 for (const points of [
  [{x:0,y:50},{x:10,y:30},{x:50,y:40},{x:55,y:0},{x:90,y:20}],
  [{x:0,y:0},{x:5,y:20},{x:70,y:30},{x:100,y:50}],
  [{x:0,y:10},{x:20,y:10},{x:40,y:10}],
 ]) {
  const segments=smoothLinePath(points).split(' C').slice(1);
  assert.equal(segments.length,points.length-1);
  segments.forEach((segment,index)=>{
   const [x1,y1,x2,y2,x3,y3]=segment.split(' ').map(Number);
   const start=points[index],end=points[index+1];
   assert.equal(x3,end.x);assert.equal(y3,end.y);
   for(let t=0;t<=1;t+=0.01){
    const u=1-t;
    const y=u*u*u*start.y+3*u*u*t*y1+3*u*t*t*y2+t*t*t*y3;
    const x=u*u*u*start.x+3*u*u*t*x1+3*u*t*t*x2+t*t*t*x3;
    assert.ok(y>=Math.min(start.y,end.y)-0.01&&y<=Math.max(start.y,end.y)+0.01);
    assert.ok(x>=start.x-0.01&&x<=end.x+0.01);
   }
  });
 }
});
test("empty, single-point and coincident readings produce finite paths",()=>{
 assert.equal(smoothLinePath([]),'');
 assert.equal(smoothLinePath([{x:5,y:7}]),'M5.00 7.00');
 const path=smoothLinePath([{x:5,y:7},{x:5,y:8}]);
 assert.equal(path,'M5.00 7.00 L5.00 8.00');
 assert.doesNotMatch(path,/NaN|Infinity/);
});
