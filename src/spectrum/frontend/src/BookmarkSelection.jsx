import {useRef} from 'react';
import {frequencyLabel, LEFT_MARGIN, RIGHT_MARGIN} from './spectrumData.js';
import {bandDraftFields, constrainBookmarkBand, draftBookmarkBand, dragBookmarkBand, resizeBookmarkBand} from './bookmarkSelection.js';

/** An explicit editing surface; it never invokes receiver navigation. */
export function BookmarkSelection({draft, change, cancel, data, layout}) {
  const surface = useRef(null);
  const drag = useRef(null);
  const bounds = [data.low, data.high];
  const width = Math.max(0, layout.width - LEFT_MARGIN - RIGHT_MARGIN);
  const band = draftBookmarkBand(draft);
  const x = hz => (hz - data.low) / (data.high - data.low) * width;
  const frequency = event => data.low + (event.clientX - surface.current.getBoundingClientRect().left) / width * (data.high - data.low);
  const apply = next => change(bandDraftFields(next));

  function down(event, edge = 'new') {
    if (!event.isPrimary || event.button !== 0 || !width) return;
    event.preventDefault(); event.stopPropagation();
    const start = frequency(event);
    drag.current = {id: event.pointerId, start, band, edge};
    event.currentTarget.setPointerCapture(event.pointerId);
    event.currentTarget.focus();
    if (edge === 'new') apply(constrainBookmarkBand(start,
      draft.bandwidth.trim() ? band?.bandwidth_hz ?? 1 : (data.high - data.low) / 20, bounds));
  }

  function move(event) {
    const active = drag.current;
    if (active?.id !== event.pointerId) return;
    const hz = frequency(event);
    if (active.edge === 'new') {
      if (Math.abs(x(hz) - x(active.start)) >= 3) apply(dragBookmarkBand(active.start, hz, bounds));
    } else if (active.edge === 'centre') {
      apply(constrainBookmarkBand(active.band.frequency_hz + hz - active.start, active.band.bandwidth_hz, bounds));
    } else apply(resizeBookmarkBand(active.band, active.edge, hz, bounds));
  }

  function key(event, edge = 'centre') {
    if (event.key === 'Escape') {event.preventDefault(); event.stopPropagation(); cancel(); return;}
    if (!band || !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault(); event.stopPropagation();
    const step = Math.max(1, (data.high - data.low) / Math.max(1, width)) * (event.shiftKey ? 10 : 1);
    const current = band.frequency_hz + (edge === 'low' ? -band.bandwidth_hz / 2 : edge === 'high' ? band.bandwidth_hz / 2 : 0);
    const next = event.key === 'Home' ? data.low : event.key === 'End' ? data.high : current + (event.key === 'ArrowRight' ? step : -step);
    apply(edge === 'centre' ? constrainBookmarkBand(next, band.bandwidth_hz, bounds) : resizeBookmarkBand(band, edge, next, bounds));
  }

  return <div ref={surface} className="bookmark-pick-surface" tabIndex={0} role="group" aria-label="Select bookmark frequency and bandwidth"
    aria-describedby="bookmark-selection-help" style={{left: LEFT_MARGIN, right: RIGHT_MARGIN, top: layout.top, bottom: 34}}
    onPointerDown={event => down(event)} onPointerMove={move}
    onPointerUp={() => {drag.current = null;}} onPointerCancel={() => {drag.current = null;}}
    onLostPointerCapture={() => {drag.current = null;}} onKeyDown={event => key(event)}>
    <span id="bookmark-selection-help" className="bookmark-pick-help">Click a signal or drag a band · Drag edges to resize · Esc cancels</span>
    {band && <>
      <div className="bookmark-picked-band" style={{left: x(band.frequency_hz - band.bandwidth_hz / 2), width: Math.max(1, band.bandwidth_hz / (data.high - data.low) * width)}} />
      <div className="bookmark-centre-line" style={{left: x(band.frequency_hz)}} />
      {['low', 'centre', 'high'].map(edge => {
        const hz = band.frequency_hz + (edge === 'low' ? -band.bandwidth_hz / 2 : edge === 'high' ? band.bandwidth_hz / 2 : 0);
        if (hz < data.low || hz > data.high) return null;
        return <button key={edge} type="button" role="slider" className={'bookmark-band-handle bookmark-band-' + edge}
          style={{left: x(hz)}} aria-label={edge === 'centre' ? 'Bookmark centre frequency' : `Bookmark ${edge === 'low' ? 'lower' : 'upper'} band edge`}
          aria-valuemin={data.low} aria-valuemax={data.high} aria-valuenow={hz} aria-valuetext={frequencyLabel(hz, true)}
          aria-describedby="bookmark-selection-help" onPointerDown={event => down(event, edge)} onKeyDown={event => key(event, edge)}>
          <span aria-hidden="true">{edge === 'centre' ? '◆' : edge === 'low' ? '◀' : '▶'}</span>
        </button>;
      })}
    </>}
  </div>;
}
