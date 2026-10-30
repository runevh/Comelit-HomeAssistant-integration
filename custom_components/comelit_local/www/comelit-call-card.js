class ComelitCallCard extends HTMLElement {
  setConfig(config) {
    if (!config.entity) throw new Error('Select the Comelit doorbell event entity.');
    this.config = config;
    if (!this.root) {
      this.root = this.attachShadow({mode: 'open'});
      this.root.innerHTML = `<ha-card header="Doorbell"><div class="content"><p class="status"></p><p class="error" role="alert"></p><button class="answer">Answer</button><button class="talk">Enable microphone</button><button class="hangup">Hang up</button><p class="hint">Experimental calls. Answering takes the call from other phones.</p></div></ha-card><style>.content{padding:0 16px 16px}button{padding:12px;margin:4px;border-radius:8px;border:1px solid var(--divider-color);color:var(--primary-text-color);background:var(--card-background-color);cursor:pointer}button:disabled{opacity:.45;cursor:default}.error{color:var(--error-color)}.hint{font-size:12px;color:var(--secondary-text-color)}</style>`;
      this.root.querySelector('.answer').onclick = () => this.answer();
      this.root.querySelector('.talk').onclick = () => this.toggleMic();
      this.root.querySelector('.hangup').onclick = () => this.hangup();
    }
    this.render();
  }
  set hass(hass) { this._hass = hass; this.connect(); }
  connectedCallback() { this.connect(); }
  disconnectedCallback() { this.cleanup(); }
  getCardSize() { return 3; }
  async connect() {
    if (!this.isConnected || !this._hass || !this.config || this.unsubscribe || this.connecting) return;
    this.entryId = this._hass.states[this.config.entity]?.attributes.entry_id;
    if (!this.entryId) { this.error = 'Enable incoming calls in the integration options and select its doorbell event entity.'; this.render(); return; }
    this.connecting = true;
    try {
      const unsub = await this._hass.connection.subscribeMessage(message => {
        if (message.kind === 'audio') this.playAudio(message);
        else {
          const oldId = this.call?.call_id;
          this.call = message;
          if (!message.call_id || (oldId && oldId !== message.call_id)) this.stopAudio();
          if (message.error) this.error = 'Call error: ' + message.error;
          this.render();
        }
      }, {type: 'comelit_local/call_subscribe', entry_id: this.entryId});
      if (!this.isConnected) await unsub(); else this.unsubscribe = unsub;
    } catch (e) { this.error = e.message || 'Unable to subscribe to calls'; this.render(); }
    finally { this.connecting = false; }
  }
  async command(action, data) {
    return this._hass.callWS({type:'comelit_local/call_control',entry_id:this.entryId,call_id:this.call?.call_id || '',action,...(data ? {data} : {})});
  }
  async answer() {
    if (this.answering || this.owns) return;
    this.answering = true;
    this.error = ''; this.render();
    try {
      this.audio = new AudioContext(); await this.audio.resume();
      this.heartbeat = setInterval(() => this.command('heartbeat').catch(() => this.stopAudio()), 2000);
      await this.command('answer'); this.owns = true; this.render();
    } catch (e) { this.error = e.message || 'Answer failed'; this.stopAudio(); this.render(); }
    finally { this.answering = false; this.render(); }
  }
  async toggleMic() {
    if (this.micStarting) return;
    if (this.mic) { this.stopMic(); this.render(); return; }
    if (!window.isSecureContext || !navigator.mediaDevices) {
      this.error = 'Microphone access requires HTTPS or localhost. This applies to desktop and mobile.'; this.render(); return;
    }
    try {
      this.micStarting = true; this.render();
      const expected = this.call?.call_id;
      const mic = await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true,autoGainControl:true},video:false});
      if (!this.owns || expected !== this.call?.call_id) { mic.getTracks().forEach(t => t.stop()); return; }
      this.mic = mic;
      await this.audio.audioWorklet.addModule('/comelit_local/microphone.js');
      if (!this.mic || !this.owns) return;
      this.node = new AudioWorkletNode(this.audio, 'comelit-microphone');
      this.source = this.audio.createMediaStreamSource(mic);
      this.node.port.onmessage = event => {
        if (!this.mic || !this.owns || this.pendingMic) return;
        let binary = ''; for (const b of event.data) binary += String.fromCharCode(b);
        this.pendingMic = true;
        this.command('microphone', btoa(binary)).catch(() => { this.stopMic(); this.error = 'Microphone stopped: connection lost'; this.render(); }).finally(() => this.pendingMic = false);
      };
      this.source.connect(this.node); this.node.connect(this.audio.destination);
      this.render();
    } catch (e) { this.stopMic(); this.error = e.message || 'Microphone unavailable'; this.render(); }
    finally { this.micStarting = false; this.render(); }
  }
  decode(byte, codec) {
    if (codec === 0) {
      byte = ~byte & 255; let value = ((byte & 15) << 3) + 132;
      value <<= (byte >> 4) & 7;
      return ((byte & 128) ? 132 - value : value - 132) / 32768;
    }
    byte ^= 0x55; let value = (byte & 15) << 4; const segment = (byte & 112) >> 4;
    value += segment === 0 ? 8 : 264; if (segment > 1) value <<= segment - 1;
    return ((byte & 128) ? value : -value) / 32768;
  }
  playAudio(message) {
    if (!this.audio || !this.owns) return;
    const bytes = atob(message.data), buffer = this.audio.createBuffer(1, bytes.length, 8000);
    const pcm = buffer.getChannelData(0);
    for (let i=0;i<bytes.length;i++) pcm[i] = this.decode(bytes.charCodeAt(i), message.codec);
    this.playAt = Math.max(this.audio.currentTime + .02, this.playAt || 0);
    if (this.playAt > this.audio.currentTime + .3) this.playAt = this.audio.currentTime + .02;
    const node = this.audio.createBufferSource(); node.buffer = buffer; node.connect(this.audio.destination); node.start(this.playAt); this.playAt += buffer.duration;
  }
  stopMic() { this.node?.disconnect(); this.source?.disconnect(); this.mic?.getTracks().forEach(t=>t.stop()); this.node = this.source = this.mic = null; }
  stopAudio() { this.stopMic(); clearInterval(this.heartbeat); this.heartbeat = null; this.audio?.close().catch(()=>{}); this.audio = null; this.playAt = 0; this.owns = false; }
  async hangup() { try { await this.command('hangup'); } catch(e) { this.error = e.message; } finally { this.stopAudio(); this.render(); } }
  cleanup() { if (this.owns) this.command('hangup').catch(()=>{}); this.stopAudio(); this.unsubscribe?.(); this.unsubscribe = null; }
  render() {
    if (!this.root) return;
    this.root.querySelector('.status').textContent = `${this.call?.entrance || 'Entrance'} — ${this.call?.state || 'Connecting'}`;
    this.root.querySelector('.error').textContent = this.error || '';
    this.root.querySelector('.answer').disabled = this.answering || this.call?.state !== 'ringing';
    this.root.querySelector('.talk').disabled = this.micStarting || !this.owns || this.call?.state !== 'connected';
    this.root.querySelector('.talk').textContent = this.mic ? 'Mute microphone' : 'Enable microphone';
    this.root.querySelector('.hangup').disabled = !this.owns;
  }
}
if (!customElements.get('comelit-call-card')) customElements.define('comelit-call-card',ComelitCallCard);
window.customCards = window.customCards || [];
window.customCards.push({type:'comelit-call-card',name:'Comelit call',description:'Answer incoming Comelit calls and explicitly enable microphone audio.'});
