import {frequencyLabel, LEFT_MARGIN, RIGHT_MARGIN} from './spectrumData.js';

function drawLine(context, x1, y1, x2, y2, color) {
  context.strokeStyle = color;
  context.beginPath();
  context.moveTo(x1, y1);
  context.lineTo(x2, y2);
  context.stroke();
}

export function drawSpectrum(canvas, layout, data, view, power, cursor, selection) {
  const {width, height, scale, top} = layout;
  const {low, high, dataLow, dataHigh, values, peaks, rejected} = data;
  const {low: powerLow, span: powerSpan} = power;
  const left = LEFT_MARGIN;
  const right = width - RIGHT_MARGIN;
  const bottom = height - 34;
  const plotHeight = Math.max(0, bottom - top);

  const pixelWidth = Math.round(width * scale);
  const pixelHeight = Math.round(height * scale);
  if (canvas.width !== pixelWidth) canvas.width = pixelWidth;
  if (canvas.height !== pixelHeight) canvas.height = pixelHeight;
  const context = canvas.getContext('2d');
  context.setTransform(scale, 0, 0, scale, 0, 0);
  context.clearRect(0, 0, width, height);
  if (right <= left || plotHeight === 0) return;

  const span = high - low;
  const binWidth = (dataHigh - dataLow) / values.length;
  const x = fraction => left + fraction * (right - left);
  const y = value => bottom - (value - powerLow) / powerSpan * plotHeight;

  context.font = '13px system-ui';
  context.fillStyle = '#a7babd';
  context.textAlign = 'right';
  const idealStep = powerSpan / Math.max(2, plotHeight / 48);
  const magnitude = 10 ** Math.floor(Math.log10(idealStep));
  const step = [1, 2, 5, 10].find(factor => factor * magnitude >= idealStep) * magnitude;
  for (let value = Math.ceil(powerLow / step) * step; value <= powerLow + powerSpan; value += step) {
    drawLine(context, left, y(value), right, y(value), '#304046');
    context.fillText(value, left - 9, y(value) + 4);
  }

  const divisions = width < 540 ? 2 : 4;
  let ticks = width < 540 ? [low, 2e9, 4e9, high] : [low, 1e9, 2e9, 3e9, 4e9, 5e9, high];
  if (view) ticks = Array.from({length: divisions + 1}, (_, index) => low + index * span / divisions);
  for (const hz of ticks) {
    const position = x((hz - low) / span);
    drawLine(context, position, top, position, bottom, '#304046');
    context.textAlign = hz === low ? 'left' : hz === high ? 'right' : 'center';
    const fullBandLabel = hz === low ? '70 MHz' : hz / 1e9 + ' GHz';
    const label = view ? frequencyLabel(hz, span < 20e6) : fullBandLabel;
    context.fillText(label, position, bottom + 22);
  }

  if (values.length) {
    const first = Math.max(0, Math.floor((low - dataLow) / binWidth) - 1);
    const last = Math.min(values.length, Math.ceil((high - dataLow) / binWidth) + 1);
    function drawTrace(trace, color) {
      context.beginPath();
      context.strokeStyle = color;
      let connected = false;
      for (let index = first; index < last; index++) {
        const value = trace[index];
        if (!Number.isFinite(value)) {
          connected = false;
          continue;
        }
        const hz = dataLow + (index + 0.5) * binWidth;
        const position = x((hz - low) / span);
        if (connected) context.lineTo(position, y(value));
        else context.moveTo(position, y(value));
        connected = true;
      }
      context.stroke();
    }

    context.save();
    context.beginPath();
    context.rect(left, top, right - left, plotHeight);
    context.clip();
    if (peaks) drawTrace(peaks, '#f6b76baa');
    drawTrace(values, rejected ? '#a9bcff' : '#66dec5');
    context.restore();
  }

  if (selection) {
    const [start, end] = selection.map(x);
    context.fillStyle = '#66dec533';
    context.fillRect(Math.min(start, end), top, Math.abs(end - start), plotHeight);
  }
  if (cursor !== null && cursor >= 0 && cursor <= 1 && values.length) {
    drawLine(context, x(cursor), top, x(cursor), bottom, '#c1d2d5');
  }
}
