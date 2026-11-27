import {clamp, FULL_RANGE} from './spectrumData.js';

export const MAX_BOOKMARK_BANDWIDTH = 56e6;

export function newBookmarkDraft(bookmark, centre) {
  return {id: bookmark?.id ?? null, frequency: String((bookmark?.frequency_hz ?? centre) / 1e6),
    bandwidth: bookmark?.bandwidth_hz == null ? '' : String(bookmark.bandwidth_hz / 1000),
    title: bookmark?.title ?? '', modulation: bookmark?.modulation ?? '', tag: bookmark?.tag ?? '',
    notes: bookmark?.notes ?? '', selecting: Boolean(bookmark)};
}

export function selectionBounds(low, high) {
  return [Math.max(FULL_RANGE[0], low), Math.min(FULL_RANGE[1], high)];
}

/** Keep both edges inside the displayed interval and the receiver's domain. */
export function constrainBookmarkBand(centre, bandwidth, bounds = FULL_RANGE) {
  const [low, high] = selectionBounds(...bounds);
  const width = clamp(bandwidth, 1, Math.min(MAX_BOOKMARK_BANDWIDTH, high - low));
  return {frequency_hz: clamp(centre, low + width / 2, high - width / 2), bandwidth_hz: width};
}

export function dragBookmarkBand(start, end, bounds) {
  const [low, high] = selectionBounds(...bounds);
  const anchor = clamp(start, low, high);
  const edge = clamp(end, Math.max(low, anchor - MAX_BOOKMARK_BANDWIDTH), Math.min(high, anchor + MAX_BOOKMARK_BANDWIDTH));
  return constrainBookmarkBand((anchor + edge) / 2, Math.abs(edge - anchor), bounds);
}

/** Resize one edge without moving the opposite edge. Edges cannot cross. */
export function resizeBookmarkBand(band, edge, frequency, bounds) {
  const [low, high] = selectionBounds(...bounds);
  const lower = band.frequency_hz - band.bandwidth_hz / 2;
  const upper = band.frequency_hz + band.bandwidth_hz / 2;
  const nextLow = edge === 'low' ? clamp(frequency, Math.max(low, upper - MAX_BOOKMARK_BANDWIDTH), upper - 1) : lower;
  const nextHigh = edge === 'high' ? clamp(frequency, lower + 1, Math.min(high, lower + MAX_BOOKMARK_BANDWIDTH)) : upper;
  return constrainBookmarkBand((nextLow + nextHigh) / 2, nextHigh - nextLow, bounds);
}

export function bandDraftFields(band) {
  return {frequency: String(band.frequency_hz / 1e6), bandwidth: String(band.bandwidth_hz / 1000)};
}

export function draftBookmarkBand(draft) {
  const frequency = Number(draft.frequency) * 1e6;
  const bandwidth = draft.bandwidth.trim() ? Number(draft.bandwidth) * 1000 : 1;
  if (!draft.frequency.trim() || !Number.isFinite(frequency) || !Number.isFinite(bandwidth)
    || frequency < FULL_RANGE[0] || frequency > FULL_RANGE[1] || bandwidth < 1 || bandwidth > MAX_BOOKMARK_BANDWIDTH) return null;
  return {frequency_hz: frequency, bandwidth_hz: bandwidth};
}
