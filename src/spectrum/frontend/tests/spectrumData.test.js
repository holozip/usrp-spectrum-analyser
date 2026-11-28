import assert from 'node:assert/strict';
import {test} from 'node:test';
import {
  constrainRange, decodeDetail, FULL_RANGE, keepImageSet, selectSpectrumData, spectrumReadout,
} from '../src/spectrumData.js';

function packet(...traces) {
  const values = traces.flat();
  const bytes = new ArrayBuffer(values.length * 4);
  const view = new DataView(bytes);
  values.forEach((value, index) => view.setFloat32(index * 4, value, true));
  return bytes;
}

test('focus stays inside the receiver band and respects the minimum span', () => {
  const [low, high] = FULL_RANGE;
  assert.equal(constrainRange(low - 1e6, high + 1e6, 32_000), null);
  assert.deepEqual(constrainRange(low - 1e6, low + 1e6, 32_000), [low, low + 2e6]);
  assert.deepEqual(constrainRange(high - 2, high - 1, 32_000), [high - 32_000, high]);
});

test('a completed image set preserves all four sources and each raw peak trace', () => {
  const frame = {low: 100e6, high: 102e6, trace: [-56, -55], detail_count: 4, peaks: true, image_set: true};
  const buffer = packet(
    [-57, -56, -55, -58], [-48, -47, -46, -49], // Raw C
    [-80, -79, -84, -83], // per-bin minimum
    [-78, -79, -63, -83], [-71, -72, -56, -77], // Raw A
    [-80, -70, -84, -76], [-75, -61, -78, -68], // Raw B
  );
  const complete = {...frame, ...decodeDetail(frame, buffer)};
  const view = [100e6, 102e6];
  const rejected = selectSpectrumData(complete, view, 'rejected', false, true);
  assert.deepEqual([...rejected.values], [-80, -79, -84, -83]);
  assert.equal(rejected.peaks, null);
  const rawA = selectSpectrumData(complete, view, 'A', false, true);
  const rawB = selectSpectrumData(complete, view, 'B', false, true);
  const rawC = selectSpectrumData(complete, view, 'C', false, true);
  assert.deepEqual([...rawA.peaks], [-71, -72, -56, -77]);
  assert.deepEqual([...rawB.peaks], [-75, -61, -78, -68]);
  assert.deepEqual([...rawC.values], [-57, -56, -55, -58]);
  assert.deepEqual([...rawC.peaks], [-48, -47, -46, -49]);
  assert.equal(spectrumReadout(rawC, view, 0.25), '100.7500 MHz · 500 kHz/bin');
  assert.deepEqual([...selectSpectrumData(complete, null, 'B', false, true).values], [-70, -76]);
});

test('truncated binary detail is rejected before it can replace a valid trace', () => {
  const frame = {trace: [0, 0], detail_count: 4, peaks: true};
  assert.throws(() => decodeDetail(frame, packet([1, 2, 3, 4])), /Unexpected spectrum detail size/);
});

test('partial image sets retain a compatible completed capture only', () => {
  const previous = {imageSet: {}, gain: 35, fft_averages: 8, capture_focus: [100e6, 102e6]};
  const next = {image_leg: 'A', gain: 35, fft_averages: 8, capture_focus: [100e6, 102e6]};
  assert.equal(keepImageSet(next, previous), true);
  assert.equal(keepImageSet({...next, fft_averages: 4}, previous), false);
  assert.equal(keepImageSet({...next, capture_focus: null}, previous), false);
  assert.equal(keepImageSet({...next, image_set: true}, previous), false);
});
