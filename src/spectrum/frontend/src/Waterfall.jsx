import {useLayoutEffect, useRef, useState} from 'react';
import {useChartWheel, watchPixelRatio} from './chartEvents.js';
import {clamp} from './spectrumData.js';
import {createWaterfallCanvas} from './waterfallCanvas.js';

const MIN_COLOR = -160;
const MAX_COLOR = 0;
const COLOR_SPAN = MAX_COLOR - MIN_COLOR;

function ColorRange({limits, setLimits}) {
  const track = useRef(null);
  const lowerInput = useRef(null);
  const upperInput = useRef(null);
  const [low, high] = limits;
  const label = value => String(value).replace('-', '−');

  function setLimit(lower, value) {
    setLimits(lower ? [Math.min(value, high - 1), high] : [low, Math.max(value, low + 1)]);
  }

  function handleTrackClick(event) {
    if (event.target !== event.currentTarget) return;
    const bounds = track.current.getBoundingClientRect();
    const fraction = clamp((event.clientX - bounds.left) / bounds.width, 0, 1);
    const value = Math.round(MIN_COLOR + fraction * COLOR_SPAN);
    const lower = value < (low + high) / 2;
    (lower ? lowerInput : upperInput).current.focus();
    setLimit(lower, value);
  }

  return (
    <div className="waterfall-scale" role="group" aria-label="Waterfall color range">
      <output id="waterfall-low-value" htmlFor="waterfall-low" aria-live="off">{label(low)}</output>
      <div className="waterfall-range" onClick={handleTrackClick}>
        <span
          className="waterfall-range-track"
          ref={track}
          aria-hidden="true"
          style={{
            '--start-space': 100 * (low - MIN_COLOR) / COLOR_SPAN + '%',
            '--end-space': 100 * (MAX_COLOR - high) / COLOR_SPAN + '%',
          }}
        />
        <input
          id="waterfall-low" ref={lowerInput} type="range" min={MIN_COLOR} max={MAX_COLOR} value={low}
          aria-label="Waterfall lower level" aria-valuetext={label(low) + ' dBFS'} aria-valuemax={high - 1}
          onChange={event => setLimit(true, event.currentTarget.valueAsNumber)}
        />
        <input
          id="waterfall-high" ref={upperInput} type="range" min={MIN_COLOR} max={MAX_COLOR} value={high}
          aria-label="Waterfall upper level" aria-valuetext={label(high) + ' dBFS'} aria-valuemin={low + 1}
          onChange={event => setLimit(false, event.currentTarget.valueAsNumber)}
        />
      </div>
      <span><output id="waterfall-high-value" htmlFor="waterfall-high" aria-live="off">{label(high)}</output> dBFS</span>
    </div>
  );
}

export function Waterfall({shown, data, view, frozen, gestures}) {
  const canvas = useRef(null);
  const rows = useRef(null);
  const renderer = useRef(null);
  const [limits, setLimits] = useState([-115, -35]);
  const [low, high] = limits;

  useLayoutEffect(() => {
    const drawing = createWaterfallCanvas(canvas.current, rows.current);
    renderer.current = drawing;
    drawing.resize();
    const observer = new ResizeObserver(drawing.resize);
    observer.observe(rows.current);
    const stopWatching = watchPixelRatio(drawing.resize);
    return () => {
      observer.disconnect();
      stopWatching();
      renderer.current = null;
    };
  }, []);

  useLayoutEffect(() => {
    renderer.current.setLimits(low, high);
  }, [low, high]);

  useLayoutEffect(() => {
    renderer.current.addCapture(shown, data, view, frozen);
  }, [shown, data, view, frozen]);

  useChartWheel(canvas, event => gestures.wheel(event, false));

  return (
    <div id="waterfall-panel">
      <div id="waterfall-label">
        <span>Waterfall · newest at top</span>
        <ColorRange limits={limits} setLimits={setLimits} />
      </div>
      <div id="waterfall-rows" ref={rows}>
        <canvas
          id="waterfall" ref={canvas} width={1024} height={160} role="img" tabIndex={0}
          aria-label="History of received power across the frequencies shown in the spectrum plot"
          aria-describedby="frequency-keys"
          {...gestures.surfaceHandlers('waterfall')}
        />
      </div>
    </div>
  );
}
