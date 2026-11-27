import assert from 'node:assert/strict';
import {test} from 'node:test';
import {bookmarkAnchor, placeBookmarkFlags} from '../src/bookmarkLayout.js';
import {DEFAULT_POWER, LEFT_MARGIN, RIGHT_MARGIN} from '../src/spectrumData.js';

const data = {low: 100e6, high: 102e6, dataLow: 100e6, dataHigh: 102e6,
  values: [-100, -80, -50, -90], peaks: [0, 0, 0, 0]};
const layout = {width: 800, height: 400, top: 64};
const bookmark = (id, frequency_hz = 101e6) => ({id: String(id), frequency_hz, title: 'Signal ' + id});

function assertPacked(flags, box) {
  for (const flag of flags) {
    assert.ok(flag.left >= LEFT_MARGIN);
    assert.ok(flag.left + flag.width <= box.width - RIGHT_MARGIN + 1e-8);
    assert.ok(flag.top >= box.top);
    assert.ok(flag.top + flag.height <= box.height - 34);
    for (const other of flags) {
      if (flag === other) continue;
      assert.ok(flag.left + flag.width <= other.left || other.left + other.width <= flag.left
        || flag.top + flag.height <= other.top || other.top + other.height <= flag.top, 'labels must not overlap');
    }
  }
}

test('anchor uses exact bookmark frequency and current displayed bin, never peak hold', () => {
  const anchor = bookmarkAnchor(bookmark(1, 101.1e6), data, layout, DEFAULT_POWER);
  assert.equal(anchor.power, -50);
  assert.equal(anchor.x, LEFT_MARGIN + .55 * (layout.width - LEFT_MARGIN - RIGHT_MARGIN));
  assert.equal(anchor.y, 366 - 70 / 120 * (366 - 64));
  const missing = bookmarkAnchor(bookmark(2), {...data, values: []}, layout, DEFAULT_POWER);
  assert.equal(missing.power, null);
  assert.equal(missing.y, 366);
});

test('offscreen bookmarks are filtered and both band edges stay inside the plot', () => {
  const flags = placeBookmarkFlags([bookmark(1, 99e6), bookmark(2, 100e6), bookmark(3, 102e6), bookmark(4, 103e6)], data, layout, DEFAULT_POWER);
  assert.deepEqual(flags.flatMap(flag => flag.members.map(member => member.id)), ['2', '3']);
  assertPacked(flags, layout);
});

test('crowded same-frequency bookmarks are deterministic, grouped and never lost', () => {
  const bookmarks = Array.from({length: 137}, (_, index) => bookmark(index));
  const flags = placeBookmarkFlags(bookmarks, data, layout, DEFAULT_POWER);
  assertPacked(flags, layout);
  assert.equal(flags.flatMap(flag => flag.members).length, bookmarks.length);
  assert.ok(flags.some(flag => flag.members.length > 1));
  assert.deepEqual(flags, placeBookmarkFlags([...bookmarks].reverse(), data, layout, DEFAULT_POWER));
});

test('mobile, tiny and short plots explicitly group rather than overlap', () => {
  for (const width of [360, 180, 99]) {
    for (const height of [200, 126]) {
      const box = {...layout, width, height};
      const bookmarks = Array.from({length: 40}, (_, index) => bookmark(index, 100e6 + index / 39 * 2e6));
      const flags = placeBookmarkFlags(bookmarks, data, box, DEFAULT_POWER);
      assertPacked(flags, box);
      assert.equal(flags.flatMap(flag => flag.members).length, 40);
    }
  }
  assert.deepEqual(placeBookmarkFlags([bookmark(1)], data, {...layout, width: 70}, DEFAULT_POWER), []);
});

test('anchors follow selected image trace data and indicate out-of-capture data', () => {
  assert.equal(bookmarkAnchor(bookmark(1), {...data, values: [-30, -30, -30, -30]}, layout, DEFAULT_POWER).power, -30);
  assert.equal(bookmarkAnchor(bookmark(1), {...data, dataLow: 101.5e6}, layout, DEFAULT_POWER).power, null);
});
