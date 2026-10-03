// Exercise the shipped inline script without a browser or provider credentials.
// Usage: node tests/web_interactions.cjs <exported-page.html>
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const html = fs.readFileSync(process.argv[2], 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
const elements = new Map();
function element(id) {
  if (!elements.has(id)) {
    const classes = new Set();
    elements.set(id, {
      style: {}, textContent: '', listeners: {},
      classList: {add: name=>classes.add(name), remove: name=>classes.delete(name),
        toggle: (name,on)=>on ? classes.add(name) : classes.delete(name), contains:name=>classes.has(name)},
      addEventListener(name,handler) {this.listeners[name]=handler;},
      setPointerCapture() {}, getContext: ()=>({setTransform() {}}),
      getBoundingClientRect: ()=>({width:200,height:id==='header' ? 108 : 50}),
      querySelector: name=>element(id+name), appendChild() {}, focus() {},
    });
  }
  return elements.get(id);
}
const context = vm.createContext({
  document: {getElementById:element, documentElement:{style:{setProperty() {}}},
    createElement:()=>element(Math.random()), createTextNode:text=>({textContent:text})},
  window: {innerWidth:390,innerHeight:844,devicePixelRatio:1,addEventListener() {}},
  ResizeObserver: class {observe() {}}, matchMedia:()=>({matches:true}),
  fetch:async()=>({json:async()=>({phase:'done',cards:[],edges:[]})}),
  performance:{now:()=>1}, requestAnimationFrame() {}, setInterval() {}, setTimeout,
  console,
});
vm.runInContext(script, context);
vm.runInContext(`
  nodes = [{sx:195,sy:476,sr:7,card:{id:'sample',name:'测试概念',definition:'测试定义',
    location:{file_path:'sample.py',start_line:1,end_line:2}}}];
`, context);
const canvas = element('cv');
const emit = (name,id,x,y,pointerType='touch') => canvas.listeners[name]({pointerId:id,clientX:x,clientY:y,pointerType,button:0});
const read = expression=>vm.runInContext(expression,context);

emit('pointerdown',1,195,476);
emit('pointerup',1,195,476);
assert.equal(element('panel').classList.contains('open'),true,'touch tap opens a card');
vm.runInContext('closePanel()',context);
emit('pointerdown',1,195,476);
const initialRotation=read('rotY');
emit('pointermove',1,250,500);
emit('pointerup',1,250,500);
assert.notEqual(read('rotY'),initialRotation,'one-finger drag rotates');
assert.equal(element('panel').classList.contains('open'),false,'drag does not open a card');

emit('pointerdown',1,150,476);
emit('pointerdown',2,250,476);
const initialZoom=read('zoom');
emit('pointermove',2,350,476);
assert.ok(read('zoom')>initialZoom,'pinch out zooms in');
emit('pointermove',2,200,476);
assert.ok(read('zoom')<initialZoom,'pinch in zooms out');
emit('pointerup',2,200,476);
emit('pointerup',1,195,476);
assert.equal(element('panel').classList.contains('open'),false,'pinch release does not open a card');
assert.equal(read('pointers.size'),0);
assert.equal(read('dragging'),false);

emit('pointerdown',3,195,476);
emit('pointercancel',3,195,476);
assert.equal(element('panel').classList.contains('open'),false,'cancelled gesture does not open a card');
assert.equal(read('dragging'),false);
assert.equal(read('pointers.size'),0);

emit('pointerdown',4,195,476,'mouse');
emit('pointerup',4,195,476,'mouse');
assert.equal(element('panel').classList.contains('open'),true,'desktop click remains usable');
assert.match(element('hint').textContent,/双指缩放/,'touch devices show touch instructions');
console.log('PASS: touch tap, drag, pinch in/out, pinch release, cancellation and desktop click');
