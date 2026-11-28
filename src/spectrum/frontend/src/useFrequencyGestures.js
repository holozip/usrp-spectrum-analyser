import {useRef, useState} from 'react';
import {wheelZoom} from './chartEvents.js';
import {
  clamp, constrainRange, FULL_RANGE, LEFT_MARGIN, minimumSpan, RIGHT_MARGIN,
} from './spectrumData.js';

function pointerPosition(event, surface, isSpectrum) {
  const bounds = surface.getBoundingClientRect();
  const left = isSpectrum ? LEFT_MARGIN : 0;
  const width = bounds.width - (isSpectrum ? LEFT_MARGIN + RIGHT_MARGIN : 0);
  return {fraction: (event.clientX - bounds.left - left) / width, width};
}

export function useFrequencyGestures({view, shown, previewRange, commitRange, cancelFocusDelay, tuneAt}) {
  const [cursor, setCursor] = useState(null);
  const [selection, setSelection] = useState(null);
  const [panningSurface, setPanningSurface] = useState(null);
  const drag = useRef(null);
  const ready = Boolean(shown?.trace.length);

  function boundedRange(low, high) {
    return constrainRange(low, high, minimumSpan(shown));
  }

  function commit(low, high, delay) {
    setCursor(null);
    commitRange(boundedRange(low, high), delay);
  }

  function zoom(fraction, factor, delay = 0) {
    const [low, high] = view ?? FULL_RANGE;
    const span = high - low;
    const nextSpan = clamp(span * factor, minimumSpan(shown), FULL_RANGE[1] - FULL_RANGE[0]);
    const anchor = low + fraction * span;
    commit(anchor - fraction * nextSpan, anchor + (1 - fraction) * nextSpan, delay);
  }

  function wheel(event, isSpectrum) {
    if (!ready || drag.current) return;
    const {fraction, width} = pointerPosition(event, event.currentTarget, isSpectrum);
    if (width <= 0 || fraction < 0 || fraction > 1) return;
    event.preventDefault();
    if (Math.abs(event.deltaX) > Math.abs(event.deltaY) && !event.ctrlKey) {
      const [low, high] = view ?? FULL_RANGE;
      const units = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? width : 1;
      const shift = (high - low) * event.deltaX * units / width;
      commit(low + shift, high + shift, 120);
    } else {
      zoom(fraction, wheelZoom(event), 120);
    }
  }

  function handleKeyDown(event) {
    if (!ready || drag.current) return;
    const [low, high] = view ?? FULL_RANGE;
    const shift = (high - low) / 10;
    switch (event.key) {
      case '+':
      case '=':
        zoom(cursor != null && cursor >= 0 && cursor <= 1 ? cursor : 0.5, 0.8, 120);
        break;
      case '-':
        zoom(cursor != null && cursor >= 0 && cursor <= 1 ? cursor : 0.5, 1.25, 120);
        break;
      case 'ArrowLeft':
        commit(low - shift, high - shift, 120);
        break;
      case 'ArrowRight':
        commit(low + shift, high + shift, 120);
        break;
      case 'Home':
        commit(...FULL_RANGE);
        break;
      default:
        return;
    }
    event.preventDefault();
  }

  function surfaceHandlers(name) {
    const isSpectrum = name === 'spectrum';
    const position = event => pointerPosition(event, event.currentTarget, isSpectrum);

    function handlePointerDown(event) {
      if (!event.isPrimary || event.button !== 0 || !ready || drag.current) return;
      const {fraction, width} = position(event);
      if (width <= 0 || fraction < 0 || fraction > 1) return;
      cancelFocusDelay();
      drag.current = {id: event.pointerId, fraction, range: view, selecting: event.shiftKey, moved: false};
      if (event.shiftKey) setSelection([fraction, fraction]);
      else setPanningSurface(name);
      event.currentTarget.setPointerCapture(event.pointerId);
    }

    function handlePointerMove(event) {
      if (!event.isPrimary) return;
      const {fraction, width} = position(event);
      setCursor(fraction);
      const active = drag.current;
      if (active?.id !== event.pointerId) return;
      const offset = fraction - active.fraction;
      active.moved ||= Math.abs(offset * width) >= 4;
      if (active.selecting) {
        setSelection([active.fraction, clamp(fraction, 0, 1)]);
      } else if (active.moved) {
        const [low, high] = active.range ?? FULL_RANGE;
        const shift = -offset * (high - low);
        previewRange(boundedRange(low + shift, high + shift));
      }
    }

    function finishDrag() {
      drag.current = null;
      setSelection(null);
      setPanningSurface(null);
    }

    function handlePointerUp(event) {
      const active = drag.current;
      if (active?.id !== event.pointerId) return;
      finishDrag();
      const point = position(event);
      const [low, high] = active.range ?? FULL_RANGE;
      if (active.selecting && Math.abs((point.fraction - active.fraction) * point.width) >= 8) {
        const end = clamp(point.fraction, 0, 1);
        const hz = part => low + part * (high - low);
        commit(hz(Math.min(active.fraction, end)), hz(Math.max(active.fraction, end)));
      } else if (!active.selecting && active.moved) {
        const shift = (active.fraction - point.fraction) * (high - low);
        commit(low + shift, high + shift);
      } else if (tuneAt && !active.selecting && point.fraction >= 0 && point.fraction <= 1) {
        tuneAt(low + point.fraction * (high - low));
      } else {
        commitRange(view);
      }
    }

    function handleLostPointerCapture(event) {
      if (drag.current?.id !== event.pointerId) return;
      const {range} = drag.current;
      finishDrag();
      commitRange(range);
    }

    return {
      className: panningSurface === name ? 'panning' : undefined,
      onPointerDown: handlePointerDown,
      onPointerMove: handlePointerMove,
      onPointerUp: handlePointerUp,
      onLostPointerCapture: handleLostPointerCapture,
      onPointerLeave: () => setCursor(null),
      onKeyDown: handleKeyDown,
    };
  }

  return {cursor, selection, setCursor, wheel, surfaceHandlers};
}
