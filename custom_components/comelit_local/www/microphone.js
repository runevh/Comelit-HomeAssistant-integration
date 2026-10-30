// Resample browser PCM to 8 kHz and emit 20 ms G.711 A-law frames.
class ComelitMicrophone extends AudioWorkletProcessor {
  constructor() { super(); this.sum = 0; this.count = 0; this.phase = 0; this.bytes = []; }
  encode(value) {
    let pcm = Math.max(-32768, Math.min(32767, Math.round(value * 32767)));
    let mask = pcm >= 0 ? 0xd5 : 0x55;
    if (pcm < 0) pcm = -pcm - 1;
    pcm = Math.min(pcm, 32767);
    let segment = 0;
    for (let limit = 255; pcm > limit && segment < 7; limit = (limit << 1) | 1) segment++;
    let encoded = segment << 4;
    encoded |= segment < 2 ? (pcm >> 4) & 15 : (pcm >> (segment + 3)) & 15;
    return encoded ^ mask;
  }
  process(inputs, outputs) {
    const input = inputs[0]?.[0];
    if (input) for (const sample of input) {
      this.sum += sample; this.count++; this.phase += 8000;
      if (this.phase >= sampleRate) {
        this.phase -= sampleRate;
        this.bytes.push(this.encode(this.sum / this.count)); this.sum = 0; this.count = 0;
        if (this.bytes.length === 160) { this.port.postMessage(new Uint8Array(this.bytes)); this.bytes = []; }
      }
    }
    // Never play the local microphone through local speakers.
    for (const channel of outputs[0] || []) channel.fill(0);
    return true;
  }
}
registerProcessor('comelit-microphone', ComelitMicrophone);
