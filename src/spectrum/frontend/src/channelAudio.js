import workletUrl from './channelAudioWorklet.js?url&no-inline';

const AUDIO_RATE = 48000;
const HEADER_SIZE = 12;

export function createChannelAudio(volume, setStatus) {
  if (!window.AudioContext || !('audioWorklet' in AudioContext.prototype)) {
    throw new Error('AudioWorklet is unavailable');
  }
  const context = new AudioContext({sampleRate: AUDIO_RATE, latencyHint: 'interactive'});
  const controller = new AbortController();
  const {signal} = controller;
  const gain = context.createGain();
  gain.gain.value = volume;
  gain.connect(context.destination);
  let node;
  let running = false;
  let minimumEpoch = 0;

  const ready = Promise.all([context.resume(), context.audioWorklet.addModule(workletUrl)]).then(() => {
    if (signal.aborted) return;
    node = new AudioWorkletNode(context, 'channel-audio', {numberOfInputs: 0, outputChannelCount: [2]});
    node.connect(gain);
  });
  // Allow setup to finish during the tune POST without an unhandled rejection.
  void ready.catch(() => {});

  async function readStream() {
    const response = await fetch('/api/listen/audio', {signal});
    if (!response.ok || !response.body) throw new Error('Channel audio is temporarily unavailable');
    const channels = Number(response.headers.get('X-Audio-Channels') || 1);
    if (channels !== 1 && channels !== 2) throw new Error('Unsupported audio format');
    const reader = response.body.getReader();
    let remainder = new Uint8Array();
    let epoch;
    let sequence;
    try {
      while (!signal.aborted) {
        const {done, value} = await reader.read();
        if (done) return;
        const buffer = new Uint8Array(remainder.length + value.length);
        buffer.set(remainder);
        buffer.set(value, remainder.length);
        const view = new DataView(buffer.buffer);
        let offset = 0;
        while (offset + HEADER_SIZE <= buffer.length) {
          const nextEpoch = view.getUint32(offset, true);
          const nextSequence = view.getUint32(offset + 4, true);
          const count = view.getUint32(offset + 8, true);
          if (count > 4096) throw new Error('Invalid Channel audio frame');
          const end = offset + HEADER_SIZE + count * channels * 2;
          if (end > buffer.length) break;
          if (nextEpoch >= minimumEpoch) {
            const heartbeat = nextEpoch === epoch && nextSequence === sequence && count === 0;
            if (!heartbeat && (nextEpoch !== epoch || nextSequence !== sequence + 1 || count === 0)) {
              node.port.postMessage(null);
            }
            if (count) {
              const samples = new Float32Array(2 * count);
              let input = offset + HEADER_SIZE;
              for (let index = 0; index < samples.length; index += 2) {
                const left = view.getInt16(input, true) / 32768;
                samples[index] = left;
                samples[index + 1] = channels === 2 ? view.getInt16(input + 2, true) / 32768 : left;
                input += channels * 2;
              }
              node.port.postMessage(samples, [samples.buffer]);
              setStatus('Playing audio');
            }
            epoch = nextEpoch;
            sequence = nextSequence;
          }
          offset = end;
        }
        remainder = buffer.slice(offset);
      }
    } finally {
      await reader.cancel().catch(() => {});
      reader.releaseLock();
    }
  }

  async function play() {
    if (running) return;
    running = true;
    try {
      await ready;
    } catch {
      if (!signal.aborted) setStatus('Could not start browser audio');
      return;
    }
    while (!signal.aborted) {
      try {
        await readStream();
      } catch {
        // Reconnect while the requested receiver is still active.
      }
      if (signal.aborted) break;
      node.port.postMessage(null);
      setStatus('Reconnecting audio…');
      await new Promise(resolve => setTimeout(resolve, 500));
      // A restarted server may start its epochs at zero again.
      if (minimumEpoch !== Infinity) minimumEpoch = 0;
    }
  }

  return {
    play,
    reset(epoch) {
      minimumEpoch = epoch;
      node?.port.postMessage(null);
    },
    volume(value) {
      gain.gain.setTargetAtTime(value, context.currentTime, 0.015);
    },
    close() {
      controller.abort();
      void context.close().catch(() => {});
    },
  };
}
