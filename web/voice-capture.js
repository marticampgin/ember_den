// Batch samples from the audio rendering thread; the output remains silent.
class DenMicrophone extends AudioWorkletProcessor {
  constructor() {
    super();
    this.block = new Float32Array(1024);
    this.offset = 0;
  }
  process(inputs) {
    const channel = inputs[0]?.[0];
    if (!channel) return true;
    for (const sample of channel) {
      this.block[this.offset++] = sample;
      if (this.offset === this.block.length) {
        this.port.postMessage(this.block, [this.block.buffer]);
        this.block = new Float32Array(1024);
        this.offset = 0;
      }
    }
    return true;
  }
}
registerProcessor('den-microphone', DenMicrophone);

