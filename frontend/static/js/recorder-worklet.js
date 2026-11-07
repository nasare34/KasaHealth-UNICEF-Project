// Runs on the audio thread and batches mic samples into ~40 ms chunks.
class KasaRecorderProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.chunkSize = 2048;
    this.buffer = new Float32Array(this.chunkSize);
    this.offset = 0;
  }

  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (!channel) return true;
    let i = 0;
    while (i < channel.length) {
      const n = Math.min(channel.length - i, this.chunkSize - this.offset);
      this.buffer.set(channel.subarray(i, i + n), this.offset);
      this.offset += n;
      i += n;
      if (this.offset === this.chunkSize) {
        let sum = 0;
        for (let k = 0; k < this.chunkSize; k++) sum += this.buffer[k] * this.buffer[k];
        const rms = Math.sqrt(sum / this.chunkSize);
        const out = this.buffer.slice(0);
        this.port.postMessage({ samples: out, rms }, [out.buffer]);
        this.offset = 0;
      }
    }
    return true;
  }
}

registerProcessor('kasa-recorder', KasaRecorderProcessor);
