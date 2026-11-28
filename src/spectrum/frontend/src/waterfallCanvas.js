import {clamp, sameRange} from './spectrumData.js';

function createPalette() {
  const canvas = document.createElement('canvas');
  canvas.width = 256;
  canvas.height = 1;
  const context = canvas.getContext('2d');
  const gradient = context.createLinearGradient(0, 0, 256, 0);
  const stops = ['#141c2b', '#265382', '#11a9a4', '#f1be61', '#fff2d7'];
  stops.forEach((color, index) => gradient.addColorStop(index / (stops.length - 1), color));
  context.fillStyle = gradient;
  context.fillRect(0, 0, canvas.width, 1);
  return context.getImageData(0, 0, canvas.width, 1).data;
}

/** Owns the waterfall canvas pixels and a ring of uncolored, per-column dBFS values. */
export function createWaterfallCanvas(canvas, container, onSignalAvailability) {
  const context = canvas.getContext('2d');
  const width = canvas.width;
  const palette = createPalette();
  const newestRow = context.createImageData(width, 1);
  let image = context.createImageData(width, canvas.height);
  let history = new Array(canvas.height);
  let head = 0;
  let count = 0;
  let captureKey;
  let lastFrame;
  let colorLow;
  let colorHigh;
  let hasSignal = false;

  function colorRow(powers, rgba, offset = 0) {
    const span = colorHigh - colorLow;
    for (let column = 0; column < width; column++) {
      const power = powers[column];
      const color = 4 * Math.round(255 * clamp((power - colorLow) / span, 0, 1));
      const pixel = offset + 4 * column;
      rgba[pixel] = palette[color];
      rgba[pixel + 1] = palette[color + 1];
      rgba[pixel + 2] = palette[color + 2];
      rgba[pixel + 3] = Number.isFinite(power) ? 255 : 0;
    }
  }

  function repaint() {
    if (!count) return;
    const rowBytes = 4 * width;
    const visibleRows = Math.min(count, canvas.height);
    for (let row = 0; row < visibleRows; row++) {
      const index = (head + row) % history.length;
      colorRow(history[index], image.data, row * rowBytes);
    }
    image.data.fill(0, visibleRows * rowBytes);
    context.putImageData(image, 0, 0);
  }

  function resize() {
    const {top, bottom} = container.getBoundingClientRect();
    const scale = window.devicePixelRatio;
    // Align both edges to device pixels so one capture stays exactly one row high.
    const firstRow = Math.ceil(top * scale);
    const height = Math.max(1, Math.floor(bottom * scale) - firstRow);
    canvas.style.top = (firstRow / scale - top) + 'px';
    canvas.style.height = (height / scale) + 'px';
    if (height === canvas.height) return;
    if (height > history.length) {
      const next = new Array(Math.max(height, 2 * history.length));
      for (let row = 0; row < count; row++) next[row] = history[(head + row) % history.length];
      history = next;
      head = 0;
    }
    canvas.height = height;
    image = context.createImageData(width, height);
    repaint();
  }

  function setLimits(low, high) {
    if (colorLow === low && colorHigh === high) return;
    colorLow = low;
    colorHigh = high;
    repaint();
  }

  function updateSignalAvailability(available) {
    if (available === hasSignal) return;
    hasSignal = available;
    onSignalAvailability(available);
  }

  function signalLevels() {
    if (!hasSignal) return null;
    const sorted = history[head].filter(Number.isFinite).sort();
    const last = sorted.length - 1;
    // Use the lower quartile to estimate the floor even with many occupied frequencies.
    return {noiseFloor: sorted[Math.floor(last / 4)], peak: sorted[last]};
  }

  function addCapture(frame, data, view, frozen) {
    if (!frame?.number) return;
    const {low, high, dataLow, dataHigh, leg, values} = data;
    const key = [low, high, dataLow, dataHigh, frame.gain, frame.fft_averages, leg].join('/');
    const {height} = canvas;
    if (key !== captureKey || frame.number < lastFrame) {
      context.clearRect(0, 0, width, height);
      count = 0;
      captureKey = key;
      lastFrame = undefined;
    }
    if (!values.length || frozen || !sameRange(frame.capture_focus, view) || lastFrame === frame.number) {
      if (!count) updateSignalAvailability(false);
      return;
    }

    const binsPerHz = values.length / (dataHigh - dataLow);
    const hzPerPixel = (high - low) / width;
    head = (head + history.length - 1) % history.length;
    const powers = history[head] ??= new Float32Array(width);
    for (let column = 0; column < width; column++) {
      const edge = (low + column * hzPerPixel - dataLow) * binsPerHz;
      const first = Math.max(0, Math.floor(edge));
      const last = Math.min(values.length, Math.ceil(edge + hzPerPixel * binsPerHz));
      let power = -Infinity;
      for (let bin = first; bin < last; bin++) {
        if (Number.isFinite(values[bin])) power = Math.max(power, values[bin]);
      }
      powers[column] = power;
    }
    colorRow(powers, newestRow.data);
    count = Math.min(count + 1, history.length);
    if (height > 1) {
      context.save();
      context.globalCompositeOperation = 'copy';
      context.drawImage(canvas, 0, 0, width, height - 1, 0, 1, width, height - 1);
      context.restore();
    }
    context.putImageData(newestRow, 0, 0);
    lastFrame = frame.number;
    updateSignalAvailability(powers.some(Number.isFinite));
  }

  return {resize, setLimits, addCapture, signalLevels};
}
