export const FULL_RANGE = [70e6, 6e9];
export const LEFT_MARGIN = 58;
export const RIGHT_MARGIN = 20;
export const DEFAULT_POWER = {low: -120, span: 120};

const DISPLAY_BINS = 4096;
const BINS_PER_OVERVIEW = 384;

export function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

export function sameRange(first, second) {
  return first === second || (first != null && second != null
    && first[0] === second[0] && first[1] === second[1]);
}

export function minimumSpan(frame) {
  const bins = frame?.levels?.[0];
  return bins?.length ? 8 * (frame.high - frame.low) / bins.length : 30_000;
}

// A null range tells the receiver to use its full sweep plan.
export function constrainRange(low, high, minSpan) {
  const [bandLow, bandHigh] = FULL_RANGE;
  const bandSpan = bandHigh - bandLow;
  const span = clamp(high - low, Math.min(minSpan, bandSpan), bandSpan);
  if (span === bandSpan) return null;
  const start = clamp((low + high - span) / 2, bandLow, bandHigh - span);
  return [start, start + span];
}

export function frequencyLabel(hz, fine = false) {
  if (hz >= 1e9 && !fine) return (hz / 1e9).toFixed(3) + ' GHz';
  return (hz / 1e6).toFixed(fine ? 4 : 1) + ' MHz';
}

function groupPeaks(values, size) {
  const grouped = new Float32Array(values.length / size);
  for (let index = 0; index < grouped.length; index++) {
    let peak = -Infinity;
    const start = index * size;
    for (let bin = start; bin < start + size; bin++) peak = Math.max(peak, values[bin]);
    grouped[index] = peak;
  }
  return grouped;
}

function zoomLevels(bins) {
  const levels = [bins];
  while (bins.length > DISPLAY_BINS && bins.length % 2 === 0) {
    bins = groupPeaks(bins, 2);
    levels.push(bins);
  }
  return levels;
}

const littleEndian = new Uint8Array(new Uint32Array([1]).buffer)[0] === 1;

/** Decode the receiver's concatenated little-endian float32 traces. */
export function decodeDetail(frame, buffer) {
  const groups = frame.trace.length;
  const length = frame.detail_count ?? groups * BINS_PER_OVERVIEW;
  const rawTraces = frame.peaks ? 2 : 1;
  // The normal payload is Raw C for a completed set, then minimum, Raw A and Raw B.
  const traceCount = rawTraces + (frame.image_set ? 1 + 2 * rawTraces : 0);
  if (groups === 0 || !Number.isInteger(length) || length <= 0 || length % groups !== 0
      || buffer.byteLength !== length * traceCount * Float32Array.BYTES_PER_ELEMENT) {
    throw new Error('Unexpected spectrum detail size');
  }
  let data;
  if (littleEndian) {
    data = new Float32Array(buffer);
  } else {
    const bytes = new DataView(buffer);
    data = Float32Array.from({length: length * traceCount}, (_, index) => bytes.getFloat32(index * 4, true));
  }

  let offset = 0;
  function takeBins() {
    const bins = data.subarray(offset, offset + length);
    offset += length;
    return bins;
  }

  const levels = zoomLevels(takeBins());
  const peakLevels = frame.peaks ? zoomLevels(takeBins()) : null;

  function readImageTrace(hasPeaks) {
    const bins = takeBins();
    return {
      trace: groupPeaks(bins, length / groups),
      levels: zoomLevels(bins),
      peakLevels: hasPeaks ? zoomLevels(takeBins()) : null,
    };
  }

  const imageSet = frame.image_set ? {
    rejected: readImageTrace(false),
    A: readImageTrace(frame.peaks),
    B: readImageTrace(frame.peaks),
  } : null;
  return {levels, peakLevels, imageSet};
}

export function keepImageSet(frame, previous) {
  return Boolean(frame.image_leg && !frame.image_set && previous?.imageSet
    && previous.antenna === frame.antenna && previous.gain === frame.gain && previous.fft_averages === frame.fft_averages
    && sameRange(previous.capture_focus, frame.capture_focus));
}

/** Pick the finest trace with about one displayed overview's worth of bins in view. */
export function selectSpectrumData(frame, view, source, waitingForImage, peakHold) {
  const [low, high] = view ?? FULL_RANGE;
  const dataLow = frame?.low ?? FULL_RANGE[0];
  const dataHigh = frame?.high ?? FULL_RANGE[1];
  const imageSet = frame?.imageSet;
  const leg = imageSet ? source : waitingForImage ? 'rejected' : null;
  const rejected = leg === 'rejected';
  const trace = rejected ? imageSet?.rejected : imageSet?.[leg] ?? frame;
  const level = view ? trace?.levels?.findIndex(bins =>
    bins.length * (high - low) / (dataHigh - dataLow) <= DISPLAY_BINS) : -1;
  const values = level >= 0 ? trace.levels[level] : trace?.trace ?? [];
  const peaks = !rejected && peakHold && level >= 0 ? trace.peakLevels?.[level] : null;
  return {low, high, dataLow, dataHigh, leg, rejected, values, peaks};
}

export function spectrumReadout({low, high, dataLow, dataHigh, values}, view, cursor) {
  const binWidth = (dataHigh - dataLow) / values.length;
  const resolution = view && values.length
    ? (binWidth / 1000).toLocaleString(undefined, {maximumFractionDigits: 2}) + ' kHz/bin'
    : '';
  if (cursor == null || cursor < 0 || cursor > 1 || !values.length) return resolution;

  const hz = low + cursor * (high - low);
  const index = Math.min(Math.floor((hz - dataLow) / binWidth), values.length - 1);
  const hasData = hz >= dataLow && hz <= dataHigh && Number.isFinite(values[index]);
  const readoutHz = hasData ? dataLow + (index + 0.5) * binWidth : hz;
  return [frequencyLabel(readoutHz, high - low < 20e6), hasData ? '' : 'No data', resolution]
    .filter(Boolean).join(' · ');
}
