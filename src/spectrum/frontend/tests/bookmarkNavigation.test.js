import assert from 'node:assert/strict';
import {test} from 'node:test';
import {observeBookmarkNavigation} from '../src/bookmarkNavigation.js';

test('first and unchanged revisions do not recall historical focus', () => {
  assert.deepEqual(observeBookmarkNavigation(undefined, 8), {revision: 8, recall: false});
  assert.deepEqual(observeBookmarkNavigation(8, 8), {revision: 8, recall: false});
  assert.deepEqual(observeBookmarkNavigation(8, undefined), {revision: 8, recall: false});
});

test('server revision reset re-baselines without recalling, then accepts new recalls', () => {
  const reset = observeBookmarkNavigation(8, 0);
  assert.deepEqual(reset, {revision: 0, recall: false});
  assert.deepEqual(observeBookmarkNavigation(reset.revision, 1), {revision: 1, recall: true});
  assert.deepEqual(observeBookmarkNavigation(1, 1), {revision: 1, recall: false});
});

test('recall after restart between polls works even when revision decreases', () => {
  assert.deepEqual(observeBookmarkNavigation(8, 1), {revision: 1, recall: true});
  assert.deepEqual(observeBookmarkNavigation(1, 2), {revision: 2, recall: true});
});
