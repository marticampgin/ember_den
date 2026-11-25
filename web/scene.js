// The observable chat state drives the pose. All frames and motion run locally.
const poses = {
  idle: {description: 'Ember is curled up asleep', tail: [37, 107], ears: [94, 78]},
  listening: {description: 'Ember sits up and listens', tail: [26, 111], ears: [93, 26]},
  thinking: {description: 'Ember is writing on a little scratchpad', tail: [26, 113], ears: [85, 33]},
  responding: {description: 'Ember raises a paw to speak', tail: [23, 79], ears: [95, 27]},
};
const art = new Map();
const sceneCSS = new URL('./scene.css', import.meta.url).href;

function getArt(pose) {
  if (!art.has(pose)) {
    const image = new Image();
    image.src = new URL(`./assets/fox-${pose}.png`, import.meta.url).href;
    art.set(pose, image.decode().then(() => image));
  }
  return art.get(pose);
}

// Local displacement keeps the paws planted while the ears flick, tail sways
// and writing paw moves. Cache the short loops and stop drawing off screen.
const loops = new Map();
function animationFrames(pose, image) {
  if (loops.has(pose)) return loops.get(pose);
  const original = document.createElement('canvas');
  const w = original.width = image.width;
  const h = original.height = image.height;
  const ctx = original.getContext('2d', {willReadFrequently: true});
  ctx.drawImage(image, 0, 0);
  const pixels = new Uint32Array(ctx.getImageData(0, 0, w, h).data.buffer);
  const {tail, ears} = poses[pose];
  const weight = (x, y, [cx, cy], rx, ry) =>
    Math.max(0, 1 - ((x - cx) / rx) ** 2 - ((y - cy) / ry) ** 2) ** 2;
  const fields = Array.from({length: w * h}, (_, index) => {
    const x = index % w, y = Math.floor(index / w);
    return [weight(x, y, tail, 39, 57), weight(x, y, ears, 36, 43),
      pose === 'thinking' ? weight(x, y, [91, 104], 27, 26) : 0,
      pose === 'responding' ? weight(x, y, [119, 90], 20, 28) : 0];
  });
  const frames = Array.from({length: 24}, (_, index) => {
    const frame = ctx.createImageData(w, h);
    const data = new Uint32Array(frame.data.buffer);
    const phase = index * 2 * Math.PI / 24;
    const sway = Math.sin(phase) * (pose === 'idle' ? 1.3 : 3.2);
    const twitch = index >= 16 && index < 20 ? [1.2, 3.5, 1.7, -1][index - 16] : 0;
    const write = Math.sin(phase * 3) * 2.2;
    const wave = Math.sin(phase * 2) * 2.7;
    for (let i = 0; i < data.length; i++) {
      const [t, e, p, a] = fields[i];
      const x = i % w - Math.round(t * sway + e * twitch + p * write);
      const y = Math.floor(i / w) - Math.round(-t * sway / 2 + p * write / 2 + a * wave);
      if (x >= 0 && x < w && y >= 0 && y < h) data[i] = pixels[y * w + x];
    }
    return frame;
  });
  loops.set(pose, frames);
  return frames;
}

class EmberScene extends HTMLElement {
  static observedAttributes = ['state'];
  constructor() {
    super();
    this.attachShadow({mode: 'open'}).innerHTML = `
      <link rel="stylesheet" href="${sceneCSS}">
      <div class="world" aria-hidden="true">
        <div class="fireflies"><i></i><i></i><i></i><i></i><i></i></div>
        <div class="actor">
          <div class="shadow"></div>
          <canvas width="135" height="150"></canvas>
          <div class="sleep"><b>z</b><b>z</b></div>
        </div>
      </div>`;
    this.media = matchMedia('(prefers-reduced-motion: reduce)');
    this.canvas = this.shadowRoot.querySelector('canvas');
    this.ctx = this.canvas.getContext('2d');
    this.updateMotion = () => this.animatePose();
    this.observer = new IntersectionObserver(([entry]) => {
      this.visible = entry.isIntersecting;
      this.animatePose();
    });
  }
  connectedCallback() {
    this.setAttribute('role', 'img');
    this.media.addEventListener('change', this.updateMotion);
    document.addEventListener('visibilitychange', this.updateMotion);
    this.observer.observe(this);
    this.setPose();
    for (const pose of Object.keys(poses)) getArt(pose).catch(() => {});
  }
  disconnectedCallback() {
    clearInterval(this.timer);
    this.observer.disconnect();
    this.media.removeEventListener('change', this.updateMotion);
    document.removeEventListener('visibilitychange', this.updateMotion);
  }
  attributeChangedCallback() {
    if (this.isConnected) this.setPose();
  }
  async setPose() {
    const state = this.getAttribute('state');
    const pose = state === 'error' ? 'listening' : (Object.hasOwn(poses, state) ? state : 'idle');
    const previous = this.pose;
    clearInterval(this.timer);
    this.poseImage = null;
    this.pose = pose;
    this.setAttribute('aria-label', `${poses[pose].description} in a woodland clearing at dusk.`);
    try {
      const image = await getArt(pose);
      if (!this.isConnected || this.pose !== pose) return;
      this.frames = null;
      clearInterval(this.timer);
      this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
      this.ctx.drawImage(image, 0, 0);
      this.dataset.pose = pose;
      this.frame = 0;
      this.poseImage = image;
      this.transition?.cancel();
      if (previous && previous !== pose && !this.media.matches && this.visible) {
        // A brief stretch on waking and a softer settle on other transitions.
        const from = previous === 'idle' ? [.74, 1.07] : [.94, 1.02];
        this.transition = this.canvas.animate([
          {transform: `scale(${from[1]}, ${from[0]})`},
          {transform: 'scale(.99, 1.02)', offset: .68},
          {transform: 'scale(1, 1)'},
        ], {duration: previous === 'idle' ? 270 : 170, easing: 'ease-out'});
      }
      this.animatePose();
    } catch {
      this.setAttribute('aria-label', 'A woodland clearing at dusk.');
    }
  }
  animatePose() {
    clearInterval(this.timer);
    if (!this.poseImage || !this.isConnected || this.media.matches || !this.visible || document.hidden) return;
    this.frames ??= animationFrames(this.pose, this.poseImage);
    this.timer = setInterval(() => {
      this.frame = (this.frame + 1) % this.frames.length;
      this.ctx.putImageData(this.frames[this.frame], 0, 0);
    }, this.pose === 'thinking' ? 72 : 105);
  }
}
customElements.define('ember-scene', EmberScene);
