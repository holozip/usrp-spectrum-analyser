/* global AudioWorkletProcessor, registerProcessor */

class ChannelAudio extends AudioWorkletProcessor {
  constructor() {
    super();
    this.samples = new Float32Array(2 * 8192);
    this.clear();
    this.port.onmessage = ({data}) => {
      if (data === null) {
        this.clear();
        return;
      }
      if (this.count + data.length > this.samples.length) this.clear();
      let position = (this.start + this.count) % this.samples.length;
      for (const value of data) {
        this.samples[position] = value;
        position = (position + 1) % this.samples.length;
      }
      this.count += data.length;
    };
  }

  clear() {
    this.start = 0;
    this.count = 0;
    this.buffering = true;
  }

  process(_inputs, outputs) {
    const [left, right] = outputs[0];
    if (this.buffering && this.count < 2 * 2400) return true;
    this.buffering = false;
    const length = Math.min(left.length, this.count / 2);
    for (let index = 0; index < length; index++) {
      left[index] = this.samples[this.start];
      right[index] = this.samples[this.start + 1];
      this.start = (this.start + 2) % this.samples.length;
    }
    this.count -= 2 * length;
    if (length < left.length) this.buffering = true;
    return true;
  }
}

registerProcessor('channel-audio', ChannelAudio);
