import assert from 'node:assert/strict';
import {test} from 'node:test';
import {bandDraftFields, constrainBookmarkBand, draftBookmarkBand, dragBookmarkBand, newBookmarkDraft, resizeBookmarkBand} from '../src/bookmarkSelection.js';

test('drafts retain legacy unknown bandwidth and share exact numeric fields', () => {
  const draft = newBookmarkDraft({id: 'old', frequency_hz: 101e6, title: 'Signal'}, 200e6);
  assert.equal(draft.bandwidth, '');
  assert.equal(draft.tag, '');
  assert.equal(draft.frequency, '101');
  assert.deepEqual(bandDraftFields({frequency_hz: 101.123456e6, bandwidth_hz: 123456}), {frequency: '101.123456', bandwidth: '123.456'});
  assert.equal(draftBookmarkBand({...draft, frequency: ''}), null);
});

test('pointer selection clamps to displayed frequencies, supports reverse drags and maximum width', () => {
  assert.deepEqual(dragBookmarkBand(101.5e6, 100.5e6, [100e6, 102e6]), {frequency_hz: 101e6, bandwidth_hz: 1e6});
  assert.deepEqual(dragBookmarkBand(101e6, 103e6, [100e6, 102e6]), {frequency_hz: 101.5e6, bandwidth_hz: 1e6});
  assert.deepEqual(dragBookmarkBand(100e6, 200e6, [70e6, 6e9]), {frequency_hz: 128e6, bandwidth_hz: 56e6});
});

test('centre dragging preserves width and keeps both edges inside displayed and hardware bounds', () => {
  assert.deepEqual(constrainBookmarkBand(70e6, 56e6), {frequency_hz: 98e6, bandwidth_hz: 56e6});
  assert.deepEqual(constrainBookmarkBand(6e9, 1e6), {frequency_hz: 5999.5e6, bandwidth_hz: 1e6});
  assert.deepEqual(constrainBookmarkBand(99e6, 1e6, [100e6, 102e6]), {frequency_hz: 100.5e6, bandwidth_hz: 1e6});
  assert.equal(constrainBookmarkBand(101e6, 0).bandwidth_hz, 1);
});

test('independent edge dragging holds the opposite edge, cannot cross and respects 56 MHz', () => {
  const band = {frequency_hz: 101e6, bandwidth_hz: 1e6};
  assert.deepEqual(resizeBookmarkBand(band, 'low', 100e6, [100e6, 102e6]), {frequency_hz: 100.75e6, bandwidth_hz: 1.5e6});
  assert.deepEqual(resizeBookmarkBand(band, 'high', 102e6, [100e6, 102e6]), {frequency_hz: 101.25e6, bandwidth_hz: 1.5e6});
  const crossed = resizeBookmarkBand(band, 'low', 102e6, [100e6, 102e6]);
  assert.equal(crossed.bandwidth_hz, 1);
  assert.equal(crossed.frequency_hz + crossed.bandwidth_hz / 2, 101.5e6);
  assert.equal(resizeBookmarkBand(band, 'high', 300e6, [70e6, 6e9]).bandwidth_hz, 56e6);
});
