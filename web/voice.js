import {api, el, notify} from './ui.js';

const sheet = document.createElement('link');
sheet.rel = 'stylesheet';
sheet.href = new URL('./voice.css', import.meta.url);
document.head.append(sheet);
const rate = 16000;
const maxSamples = rate * 20;
export const micIcon = '<svg aria-hidden="true" width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round"><rect x="9" y="2" width="6" height="13" rx="3"/><path d="M5 10v2a7 7 0 0 0 14 0v-2m-7 9v3m-4 0h8"/></svg>';
const speakerIcon = '<svg aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round"><path d="M3 9h4l5-4v14l-5-4H3zM16 8a6 6 0 0 1 0 8m3-11a10 10 0 0 1 0 14"/></svg>';
const getPref = name => localStorage.getItem('ember.voice.' + name) || '';
const setPref = (name, value) => localStorage.setItem('ember.voice.' + name, value);
const seconds = ms => (ms / 1000).toFixed(1) + ' s';

function recognizers(status) {
  return status.recognizer_options || (status.recognizers || []).map(id => ({id, name:id, culture:id}));
}

function resolvePreference(key, available, legacy = []) {
  const saved = getPref(key);
  if (!saved || available.includes(saved)) return saved;
  const replacement = legacy.find(item => item.culture === saved)?.id || '';
  setPref(key, replacement);
  return replacement;
}

function wavBase64(parts, count) {
  const bytes = new Uint8Array(44 + count * 2);
  const view = new DataView(bytes.buffer);
  const tag = (offset, text) => { for (const ch of text) bytes[offset++] = ch.charCodeAt(0); };
  tag(0, 'RIFF'); view.setUint32(4, bytes.length - 8, true); tag(8, 'WAVEfmt ');
  view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
  view.setUint32(24, rate, true); view.setUint32(28, rate * 2, true);
  view.setUint16(32, 2, true); view.setUint16(34, 16, true); tag(36, 'data');
  view.setUint32(40, count * 2, true);
  let offset = 44;
  for (const part of parts) for (const v of part) {
    view.setInt16(offset, Math.max(-1, Math.min(1, v)) * (v < 0 ? 32768 : 32767), true);
    offset += 2;
  }
  let binary = '';
  for (let i = 0; i < bytes.length; i += 8192) binary += String.fromCharCode(...bytes.subarray(i, i + 8192));
  return btoa(binary);
}

function* sentences(text) {
  let rest = text.trim();
  while (rest) {
    if (rest.length <= 420) { yield rest; return; }
    const head = rest.slice(0, 420);
    const breaks = [...head.matchAll(/[.!?]\s+|\n+/g)];
    const cut = breaks.length ? breaks.at(-1).index + 1 : (head.lastIndexOf(' ') > 0 ? head.lastIndexOf(' ') : head.length);
    yield rest.slice(0, cut).trim();
    rest = rest.slice(cut).trim();
  }
}

export function readButton(text, getVoice) {
  const button = el('button', 'read-turn');
  button.type = 'button';
  button.innerHTML = speakerIcon;
  button.append('Read aloud');
  button.onclick = () => {
    const voice = getVoice();
    if (voice?.handsFree) voice.stop('Hands-free ended.');
    voice?.speak(text);
  };
  return button;
}

export class VoiceControls {
  constructor(page, input, changed, {submitTurn, interruptTurn}) {
    this.page = page;
    this.input = input;
    this.changed = changed;
    this.submitTurn = submitTurn;
    this.interruptTurn = interruptTurn;
    this.handsFree = false;
    this.toggle = page.querySelector('[data-hands-free]');
    this.toggle.onclick = () => this.handsFree ? this.stop('Hands-free ended.') : this.startHandsFree();
    this.onVisibility = () => { if (document.hidden && this.handsFree) this.stop('Hands-free ended while the tab was hidden.'); };
    document.addEventListener('visibilitychange', this.onVisibility);
    this.onPageHide = () => { if (this.handsFree) this.stop('Hands-free ended.'); };
    window.addEventListener('pagehide', this.onPageHide);
    this.mic = page.querySelector('[data-voice-mic]');
    this.stopButton = page.querySelector('[data-voice-stop]');
    this.label = page.querySelector('[data-voice-status]');
    this.serial = 0;
    this.mode = 'idle';
    this.mic.innerHTML = micIcon;
    this.mic.onclick = () => {
      if (this.mode === 'recording') this.finishRecording();
      else if (['opening', 'transcribing'].includes(this.mode)) this.stop('Dictation canceled.');
      else this.record();
    };
    this.stopButton.onclick = () => this.stop('Voice stopped.');
  }

  setMode(mode, message) {
    this.mode = mode;
    this.label.textContent = this.handsFree ? 'Hands-free · ' + (message || 'Microphone off.') : message;
    const target = this.page.querySelector(this.handsFree ? '.voice-feedback' : '.composer-actions');
    if (this.toggle.parentElement !== target) {
      const focused = document.activeElement === this.toggle;
      target.append(this.toggle);
      if (focused) this.toggle.focus({preventScroll:true});
    }
    this.page.classList.toggle('hands-free-active', this.handsFree);
    this.toggle.setAttribute('aria-pressed', String(this.handsFree));
    this.toggle.textContent = this.handsFree ? 'End hands-free' : 'Start hands-free';
    this.mic.disabled = this.handsFree;
    this.input.readOnly = this.handsFree;
    this.input.placeholder = this.handsFree ? 'Hands-free conversation…' : 'Message Ember…';
    const recording = ['opening', 'recording', 'transcribing'].includes(mode);
    this.mic.setAttribute('aria-pressed', String(recording));
    const title = mode === 'recording' ? 'Finish dictation' : recording ? 'Cancel dictation' : 'Dictate a message';
    this.mic.title = title;
    this.mic.setAttribute('aria-label', title);
    this.stopButton.hidden = this.handsFree || mode === 'idle';
    this.stopButton.textContent = mode === 'recording' || mode === 'opening' ? 'Cancel recording' : mode === 'transcribing' ? 'Cancel transcription' : 'Stop voice';
    this.changed();
  }

  async context() {
    if (!this.audio || this.audio.state === 'closed') this.audio = new AudioContext({sampleRate: rate});
    if (this.audio.state === 'suspended') await this.audio.resume();
    return this.audio;
  }

  releaseMic() {
    this.processor?.disconnect();
    this.source?.disconnect();
    this.stream?.getTracks().forEach(track => track.stop());
    if (this.processor) this.processor.port.onmessage = null;
    this.processor = this.source = this.stream = null;
  }

  startHandsFree() {
    if (this.disposed || document.hidden) return;
    this.handsFree = true;
    this.lastUtterance = '';
    this.savedDraft = this.input.value;
    this.input.value = '';
    this.input.dispatchEvent(new Event('input'));
    this.record();
  }

  stop(message = '', interrupt = true) {
    const endSession = this.handsFree;
    this.handsFree = false;
    this.reset(message);
    if (endSession) {
      this.dictation = null;
      this.input.value = this.savedDraft;
      this.input.dispatchEvent(new Event('input'));
      if (interrupt) this.interruptTurn();
    }
  }

  reset(message = '') {
    this.serial++;
    clearTimeout(this.rearmTimer);
    this.parts = [];
    this.releaseMic();
    this.request?.abort();
    this.request = null;
    if (this.player) {
      try { this.player.stop(); } catch { /* Playback finished naturally. */ }
      this.player = null;
    }
    this.setMode('idle', message);
  }

  dispose() {
    this.disposed = true;
    document.removeEventListener('visibilitychange', this.onVisibility);
    window.removeEventListener('pagehide', this.onPageHide);
    this.stop();
    this.audio?.close().catch(() => {});
  }

  async record() {
    this.reset();
    const serial = this.serial;
    try {
      this.setMode('opening', 'Opening the local microphone…');
      if (!navigator.mediaDevices?.getUserMedia || !window.AudioWorkletNode) throw new Error('Microphone capture requires a browser with Web Audio on localhost.');
      const audio = await this.context();
      const status = await api('/api/voice/status');
      if (serial !== this.serial) return;
      if (!status.recognizers?.length) throw new Error(status.error || 'No local English recognizer is available. Check Voice settings.');
      if (this.handsFree && !status.voices?.length) throw new Error('Hands-free needs an installed English voice to read replies.');
      const options = recognizers(status);
      this.recognizer = resolvePreference('recognizer', options.map(item => item.id), options);
      const device = getPref('device');
      const constraints = {channelCount:1, echoCancellation:true};
      let stream;
      try {
        stream = await navigator.mediaDevices.getUserMedia({audio:{...constraints, ...(device ? {deviceId:{exact:device}} : {})}});
      } catch (error) {
        if (serial !== this.serial) return;
        if (!device || !['NotFoundError', 'OverconstrainedError'].includes(error.name)) throw error;
        // Device IDs can rotate after revoked permissions or removal of a microphone.
        setPref('device', '');
        stream = await navigator.mediaDevices.getUserMedia({audio:constraints});
      }
      if (serial !== this.serial) { stream.getTracks().forEach(t => t.stop()); return; }
      this.stream = stream;
      if (!this.worklet) this.worklet = audio.audioWorklet.addModule('./voice-capture.js');
      await this.worklet;
      if (serial !== this.serial) return;
      const processor = this.processor = new AudioWorkletNode(audio, 'den-microphone');
      const source = this.source = audio.createMediaStreamSource(stream);
      processor.onprocessorerror = () => {
        if (serial === this.serial) this.stop('The microphone processor stopped. Start dictation again.');
      };
      this.parts = [];
      this.count = this.voiced = this.lastVoice = this.lastSpeechAt = 0;
      this.setMode('recording', this.handsFree ? 'Listening. Pause to send automatically.' : 'Listening… pause or select the microphone to finish.');
      processor.port.onmessage = ({data}) => {
        if (serial !== this.serial || this.mode !== 'recording') return;
        const samples = data.subarray(0, maxSamples - this.count);
        this.parts.push(samples);
        this.count += samples.length;
        let energy = 0;
        for (const value of samples) energy += value * value;
        if (energy / samples.length > 0.00012) {
          this.voiced += samples.length;
          this.lastVoice = this.count;
          this.lastSpeechAt = performance.now();
        }
        const paused = this.voiced > .16 * rate && this.count - this.lastVoice > 1.15 * rate;
        if (this.handsFree && this.count >= maxSamples && !paused) {
          this.stop(this.voiced > .16 * rate ? 'Hands-free ended at the 20-second turn limit. Try a shorter utterance.' : 'Hands-free ended after 20 seconds of silence.');
        } else if (this.count >= maxSamples || paused) this.finishRecording();
      };
      for (const track of stream.getTracks()) track.onended = () => {
        if (serial !== this.serial || this.mode !== 'recording') return;
        if (this.handsFree) this.stop('Hands-free ended because the microphone disconnected.');
        else this.finishRecording();
      };
      source.connect(processor).connect(audio.destination);
    } catch (error) {
      if (serial !== this.serial) return;
      const friendly = {NotAllowedError: 'Microphone access was denied. Allow it in your browser or type a message.',
        NotFoundError: 'No microphone was found. Choose a connected input in Settings.',
        OverconstrainedError: 'The selected microphone is unavailable. Choose another input in Settings.'};
      this.stop(friendly[error.name] || error.message);
    }
  }

  async finishRecording() {
    if (this.mode !== 'recording') return;
    this.releaseMic();
    if (this.count < rate / 5) { this.stop('The recording was too short. Try again.'); return; }
    const serial = this.serial;
    const endedAt = this.lastSpeechAt || performance.now();
    try {
      this.setMode('transcribing', 'Transcribing on this device…');
      const audio = wavBase64(this.parts, this.count);
      this.parts = [];
      this.request = new AbortController();
      const result = await api('/api/voice/transcribe', {method:'POST', body:{audio, recognizer:this.recognizer}, signal:this.request.signal});
      if (serial !== this.serial) return;
      if (this.recognizer && result.recognizer && result.recognizer !== this.recognizer) setPref('recognizer', '');
      this.dictation = {recognition: result.elapsed_ms, endedAt};
      if (this.handsFree) {
        const transcript = result.text?.trim() || '';
        const normalized = transcript.toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
        if (!normalized || normalized === this.lastUtterance) {
          this.stop(normalized ? 'Hands-free ended after a repeated utterance.' : 'Hands-free ended without recognizable speech.');
          return;
        }
        this.lastUtterance = normalized;
        if (!this.submitTurn(transcript)) this.stop('Hands-free ended because the conversation is busy.');
        return;
      }
      this.input.value = [this.input.value.trimEnd(), result.text].filter(Boolean).join(' ').slice(0, this.input.maxLength);
      this.input.dispatchEvent(new Event('input'));
      this.input.focus();
      this.setMode('idle', 'Transcribed in ' + seconds(result.elapsed_ms) + '. Review and send your message.');
    } catch (error) { if (serial === this.serial) this.stop(error.message); }
  }

  sending() {
    const spoken = this.dictation;
    this.dictation = null;
    this.reset();
    if (this.handsFree) this.setMode('waiting', 'Waiting for Ember. Microphone off.');
    if (!spoken || (!this.handsFree && getPref('autoplay') === 'off')) return null;
    return {...spoken, sentAt: performance.now(), serial:this.serial, handsFree:this.handsFree};
  }

  async speak(text, turn) {
    if (!text?.trim() || this.disposed) return;
    this.reset();
    const serial = this.serial;
    let first = true, metric = '';
    try {
      this.setMode('synthesizing', 'Preparing local speech…');
      const audio = await this.context();
      this.request = new AbortController();
      const status = await api('/api/voice/status', {signal:this.request.signal});
      if (serial !== this.serial) return;
      let voice = resolvePreference('voice', status.voices || []);
      for (const segment of sentences(text)) {
        if (serial !== this.serial) return;
        const result = await api('/api/voice/synthesize', {
          method:'POST', body:{text:segment, voice}, signal:this.request.signal,
        });
        if (serial !== this.serial) return;
        if (voice && result.voice && result.voice !== voice) {
          voice = '';
          setPref('voice', '');
        }
        const bytes = Uint8Array.from(atob(result.audio), c => c.charCodeAt(0));
        const decoded = await audio.decodeAudioData(bytes.buffer);
        if (serial !== this.serial) return;
        const source = this.player = audio.createBufferSource();
        source.buffer = decoded;
        source.connect(audio.destination);
        const finished = new Promise(resolve => { source.onended = resolve; });
        source.start();
        if (first) {
          const timings = [];
          if (turn) {
            const now = performance.now();
            const interval = turn.handsFree ? 'Speech end to first audio' : 'Speech end to first audio (including review)';
            timings.push('Recognition ' + seconds(turn.recognition), 'Send to first audio ' + seconds(now - turn.sentAt),
              interval + ' ' + seconds(now - turn.endedAt));
          }
          timings.push('Synthesis ' + seconds(result.elapsed_ms));
          metric = timings.join(' · ');
          first = false;
        }
        this.setMode('speaking', 'Speaking. ' + metric);
        await finished;
        source.disconnect();
      }
      if (serial === this.serial) {
        this.player = null;
        if (this.handsFree) {
          this.setMode('rearming', 'Reply finished. Listening again shortly.');
          // The capture stream remains closed while speaker audio and its short acoustic tail play.
          this.rearmTimer = setTimeout(() => {
            if (serial === this.serial && this.handsFree && !document.hidden) this.record();
          }, 650);
        } else this.setMode('idle', metric);
      }
    } catch (error) { if (serial === this.serial) this.stop(error.message); }
  }
}

export async function mountVoiceSettings(page) {
  const section = el('section', 'voice-settings');
  section.innerHTML = '<h2>Voice</h2><p data-engine>Checking local speech…</p>';
  page.append(section);
  try {
    const info = await api('/api/voice/status');
    if (!section.isConnected) return;
    section.querySelector('[data-engine]').textContent = info.error || (info.provider + ' · ' + recognizers(info).length + ' English recognizer' + (recognizers(info).length === 1 ? '' : 's'));
    function select(label, key, choices, fallback, legacy) {
      const row = el('div','field-row');
      const caption = el('label','',label);
      const input = el('select');
      input.id = 'voice-' + key; caption.htmlFor = input.id;
      input.add(new Option(fallback, ''));
      for (const [id,name] of choices) input.add(new Option(name,id));
      input.value = resolvePreference(key, choices.map(([id]) => id), legacy);
      input.onchange = () => { setPref(key,input.value); notify('Voice preference saved.'); };
      row.append(caption,input); section.append(row);
    }
    const options = recognizers(info);
    select('Speech recognizer', 'recognizer', options.map(item => [item.id, item.name + (item.culture && item.culture !== item.name ? ' · ' + item.culture : '')]), 'Default English recognizer', options);
    select('Spoken voice','voice',(info.voices || []).map(v => [v,v]),'Default English voice');
    let devices = [];
    try { devices = await navigator.mediaDevices?.enumerateDevices() || []; } catch { /* Default input is still selectable. */ }
    const microphones = devices.filter(d => d.kind === 'audioinput' && d.deviceId && d.deviceId !== 'default');
    select('Microphone','device',microphones.map((d,i) => [d.deviceId,d.label || 'Microphone ' + (i+1)]),'System default microphone');
    const row = el('div','field-row');
    const label = el('label','','Read replies to dictated messages aloud');
    const input = el('input','switch');
    input.id='voice-autoplay'; input.type='checkbox'; input.setAttribute('role','switch'); input.checked = getPref('autoplay') !== 'off';
    label.htmlFor=input.id;
    input.onchange = () => {setPref('autoplay',input.checked ? 'on' : 'off'); notify('Voice preference saved.');};
    row.append(label,input); section.append(row);
  } catch (error) { section.querySelector('[data-engine]').textContent = error.message; }
}

