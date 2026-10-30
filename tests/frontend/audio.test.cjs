const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function microphone(rate) {
  let Processor;
  const context = {sampleRate:rate, AudioWorkletProcessor:class {constructor(){this.port={postMessage:()=>{}}}},registerProcessor:(_name,value)=>Processor=value};
  vm.runInNewContext(fs.readFileSync('custom_components/comelit_local/www/microphone.js','utf8'),context);
  return new Processor();
}

test('A-law zero, sign and peak vectors',()=>{
  const mic=microphone(48000);
  assert.equal(mic.encode(0),0xd5);
  assert.equal(mic.encode(-1/32767),0x55);
  assert.equal(mic.encode(1),0xaa);
  assert.equal(mic.encode(-1),0x2a);
});

for(const rate of [44100,48000]) test(`20ms microphone frame at ${rate}Hz, no local speaker echo`,()=>{
  const mic=microphone(rate), frames=[];
  mic.port.postMessage=frame=>frames.push(frame);
  const output=new Float32Array(rate/50).fill(1);
  mic.process([[new Float32Array(rate/50)]],[[output]]);
  assert.equal(frames.length,1);
  assert.equal(frames[0].length,160);
  assert.ok(frames[0].every(b=>b===0xd5));
  assert.ok(output.every(sample=>sample===0));
});

test('No microphone input produces no network frames',()=>{
  const mic=microphone(48000);
  mic.port.postMessage=()=>assert.fail('Unexpected microphone frame');
  mic.process([],[[new Float32Array(128)]]);
});

test('Playback decoder handles A-law and mu-law independently',()=>{
  let Card;
  const context={HTMLElement:class{},customElements:{get:()=>undefined,define:(_name,type)=>Card=type},window:{}};
  vm.runInNewContext(fs.readFileSync('custom_components/comelit_local/www/comelit-call-card.js','utf8'),context);
  const card=new Card();
  assert.equal(card.decode(0xff,0),0);
  assert.equal(card.decode(0xd5,8),8/32768);
  assert.equal(card.decode(0x55,8),-8/32768);
  assert.equal(card.decode(0xaa,8),32256/32768);
});
