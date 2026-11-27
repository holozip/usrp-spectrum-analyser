import {clamp, LEFT_MARGIN, RIGHT_MARGIN} from './spectrumData.js';

/** Match the selected displayed trace, not the separate peak-hold array. */
export function bookmarkAnchor(bookmark, data, layout, power) {
  const {frequency_hz: hz} = bookmark;
  const {low, high, dataLow, dataHigh, values} = data;
  if (!Number.isFinite(hz) || hz < low || hz > high || high <= low) return null;
  const bottom = layout.height - 34;
  const bin = clamp(Math.floor((hz - dataLow) / (dataHigh - dataLow) * values.length), 0, values.length - 1);
  const value = hz >= dataLow && hz <= dataHigh ? values[bin] : undefined;
  return {...bookmark,
    x: LEFT_MARGIN + (hz - low) / (high - low) * (layout.width - LEFT_MARGIN - RIGHT_MARGIN),
    y: Number.isFinite(value) ? clamp(bottom - (value - power.low) / power.span * (bottom - layout.top), layout.top, bottom) : bottom,
    power: Number.isFinite(value) ? value : null,
  };
}

/** Bounded grid cells guarantee non-overlap; dense neighbours share an explicit flag.
 * Each member keeps its own exact-frequency anchor, even in an overflow group.
 */
export function placeBookmarkFlags(bookmarks, data, layout, power) {
  const left = LEFT_MARGIN;
  const width = layout.width - LEFT_MARGIN - RIGHT_MARGIN;
  const height = layout.height - 34 - layout.top;
  if (width < 1 || height < 24) return [];
  const anchors = bookmarks.map(bookmark => bookmarkAnchor(bookmark, data, layout, power))
    .filter(Boolean).sort((a, b) => a.x - b.x || a.id.localeCompare(b.id));
  if (!anchors.length) return [];
  const gap = 6;
  const labelWidth = Math.min(156, width);
  const columns = Math.max(1, Math.floor((width + gap) / (labelWidth + gap)));
  const rows = Math.max(1, Math.min(3, Math.floor(height * 0.45 / 28)));
  const capacity = columns * rows;
  const groupSize = Math.ceil(anchors.length / capacity);
  const groups = [];
  for (let index = 0; index < anchors.length; index += groupSize) groups.push(anchors.slice(index, index + groupSize));
  const usedColumns = Math.min(columns, groups.length);
  const cellWidth = (width + gap) / usedColumns;
  return groups.map((members, index) => {
    const column = index % usedColumns;
    const row = Math.floor(index / usedColumns);
    const centre = members.reduce((sum, member) => sum + member.x, 0) / members.length;
    const cellLeft = left + column * cellWidth;
    return {members, width: labelWidth, height: 22,
      left: clamp(centre - labelWidth / 2, cellLeft, Math.min(left + width - labelWidth, cellLeft + cellWidth - gap - labelWidth)),
      top: layout.top + row * 28,
    };
  });
}
