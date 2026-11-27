import {useLayoutEffect, useRef, useState} from 'react';
import {useChartWheel, watchPixelRatio, wheelZoom} from './chartEvents.js';
import {drawSpectrum} from './drawSpectrum.js';
import {clamp, DEFAULT_POWER, frequencyLabel, LEFT_MARGIN} from './spectrumData.js';
import {BookmarkFlags} from './BookmarkFlags.jsx';
import {BookmarkSelection} from './BookmarkSelection.jsx';

const MIN_POWER = -160;
const MAX_POWER = 20;
const MIN_POWER_SPAN = 10;

function PowerAxis({layout, power, setPower, clearCursor}) {
  const axis = useRef(null);
  const drag = useRef(null);
  const [panning, setPanning] = useState(false);

  function changePower(low, span) {
    setPower({low: clamp(low, MIN_POWER, MAX_POWER - span), span});
  }

  function zoom(fraction, factor) {
    const span = clamp(power.span * factor, MIN_POWER_SPAN, MAX_POWER - MIN_POWER);
    changePower(power.low + fraction * (power.span - span), span);
  }

  useChartWheel(axis, event => {
    if (drag.current || Math.abs(event.deltaY) <= Math.abs(event.deltaX)) return;
    event.preventDefault();
    const bounds = axis.current.getBoundingClientRect();
    const fraction = clamp((bounds.bottom - event.clientY) / bounds.height, 0, 1);
    zoom(fraction, wheelZoom(event));
  });

  function handlePointerDown(event) {
    if (!event.isPrimary || event.button !== 0 || drag.current) return;
    drag.current = {id: event.pointerId, y: event.clientY, low: power.low};
    setPanning(true);
    event.currentTarget.setPointerCapture(event.pointerId);
  }

  function handlePointerMove(event) {
    if (drag.current?.id !== event.pointerId) return;
    const offset = (event.clientY - drag.current.y) / event.currentTarget.clientHeight;
    changePower(drag.current.low + offset * power.span, power.span);
  }

  function handleLostPointerCapture(event) {
    if (drag.current?.id !== event.pointerId) return;
    drag.current = null;
    setPanning(false);
  }

  function handleKeyDown(event) {
    switch (event.key) {
      case '+':
      case '=':
        zoom(0.5, 0.8);
        break;
      case '-':
        zoom(0.5, 1.25);
        break;
      case 'ArrowUp':
        changePower(power.low + power.span / 10, power.span);
        break;
      case 'ArrowDown':
        changePower(power.low - power.span / 10, power.span);
        break;
      case 'Home':
        setPower(DEFAULT_POWER);
        break;
      default:
        return;
    }
    event.preventDefault();
  }

  return (
    <div
      id="power-axis"
      ref={axis}
      className={panning ? 'panning' : undefined}
      style={{top: layout.top, height: Math.max(0, layout.height - 34 - layout.top), width: LEFT_MARGIN}}
      role="group"
      tabIndex={0}
      aria-label={`Power axis, ${Math.round(power.low)} to ${Math.round(power.low + power.span)} dBFS`}
      aria-describedby="power-keys"
      title="dBFS axis · Drag to pan · Scroll to scale · Double-click to reset"
      onPointerEnter={clearCursor}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onLostPointerCapture={handleLostPointerCapture}
      onDoubleClick={() => setPower(DEFAULT_POWER)}
      onKeyDown={handleKeyDown}
    />
  );
}

export function SpectrumPlot({toolsRef, data, view, gestures, bookmarks, inspectBookmarks, bookmarkSelection}) {
  const plot = useRef(null);
  const [power, setPower] = useState(DEFAULT_POWER);
  const [layout, setLayout] = useState({width: 0, height: 0, scale: 1, top: 0});
  const bookmarksVisible = bookmarks != null;

  useLayoutEffect(() => {
    const canvas = plot.current;
    const tools = toolsRef.current;
    function measure() {
      const {width, height} = canvas.getBoundingClientRect();
      const scale = window.devicePixelRatio;
      const top = tools.offsetTop + tools.offsetHeight + 5;
      setLayout(previous => previous.width === width && previous.height === height
        && previous.scale === scale && previous.top === top ? previous : {width, height, scale, top});
    }
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(canvas);
    observer.observe(tools);
    const stopWatching = watchPixelRatio(measure);
    return () => {
      observer.disconnect();
      stopWatching();
    };
  }, [toolsRef, bookmarksVisible]);

  const {cursor, selection} = gestures;
  useLayoutEffect(() => {
    drawSpectrum(plot.current, layout, data, view, power, cursor, selection);
  }, [layout, data, view, power, cursor, selection]);

  useChartWheel(plot, event => {if (!bookmarkSelection) gestures.wheel(event, true);});
  const fine = data.high - data.low < 20e6;
  const band = view ? frequencyLabel(data.low, fine) + ' to ' + frequencyLabel(data.high, fine) : '70 MHz to 6 GHz';
  return (
    <>
      <canvas
        id="plot"
        ref={plot}
        role="img"
        tabIndex={0}
        aria-label={'Received power in dBFS across ' + band}
        aria-describedby="frequency-keys"
        {...(bookmarkSelection ? {onKeyDown: event => {
          if (event.key === 'Escape') bookmarkSelection.cancel();
        }} : gestures.surfaceHandlers('spectrum'))}
      />
      <PowerAxis layout={layout} power={power} setPower={setPower} clearCursor={() => gestures.setCursor(null)} />
      {bookmarks && <BookmarkFlags bookmarks={bookmarks} data={data} layout={layout} power={power} inspect={inspectBookmarks} />}
      {bookmarkSelection && <BookmarkSelection {...bookmarkSelection} data={data} layout={layout} />}
    </>
  );
}
