import {useLayoutEffect, useRef, useState} from 'react';
import {useChartWheel, watchPixelRatio} from './chartEvents.js';
import {clamp, channelPassband} from './spectrumData.js';
import {createWaterfallCanvas} from './waterfallCanvas.js';

const MIN_COLOR = -160;
const MAX_COLOR = 0;
const COLOR_SPAN = MAX_COLOR - MIN_COLOR;
const AUTO_HEADROOM = 3;
const MIN_AUTO_SPAN = 12;

function ColorRange({limits, setLimits, autoRange, canAutoRange}) {
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
      <button type="button" onClick={autoRange} disabled={!canAutoRange}
        title="Set color limits from the noise floor and strongest peak">Auto range</button>
    </div>
  );
}

export function Waterfall({shown, data, view, frozen, gestures, channel}) {
  const canvas = useRef(null);
  const rows = useRef(null);
  const renderer = useRef(null);
  const [limits, setLimits] = useState([-115, -35]);
  const [canAutoRange, setCanAutoRange] = useState(false);
  const [low, high] = limits;
  const passband = channelPassband(channel, data.low, data.high);

  useLayoutEffect(() => {
    const drawing = createWaterfallCanvas(canvas.current, rows.current, setCanAutoRange);
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

  function autoRange() {
    const signal = renderer.current.signalLevels();
    if (!signal) return;
    const {noiseFloor, peak} = signal;
    const bottom = clamp(Math.floor(noiseFloor - AUTO_HEADROOM), MIN_COLOR, MAX_COLOR - MIN_AUTO_SPAN);
    const top = clamp(Math.ceil(peak + AUTO_HEADROOM), bottom + MIN_AUTO_SPAN, MAX_COLOR);
    setLimits([bottom, top]);
  }

  useChartWheel(canvas, event => gestures.wheel(event, false));

  return (
    <div id="waterfall-panel">
      <div id="waterfall-label">
        <span>Waterfall · newest at top</span>
        <ColorRange limits={limits} setLimits={setLimits} autoRange={autoRange} canAutoRange={canAutoRange} />
      </div>
      <div id="waterfall-rows" ref={rows}>
        <canvas
          id="waterfall" ref={canvas} width={1024} height={160} role="img" tabIndex={0}
          aria-label="History of received power across the frequencies shown in the spectrum plot"
          aria-describedby="frequency-keys"
          {...gestures.surfaceHandlers('waterfall')}
        />
        {passband && <div className="channel-waterfall-passband" aria-hidden="true"
          style={{left: `${100 * passband[0]}%`, width: `${100 * (passband[1] - passband[0])}%`}} />}
      </div>
    </div>
  );
}
