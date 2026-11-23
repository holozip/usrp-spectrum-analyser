import {useEffect, useRef, useState} from 'react';
import {clamp} from './spectrumData.js';

function resizeBounds(panel, plotWindow, divider) {
  const top = plotWindow.getBoundingClientRect().top;
  const bottom = panel.getBoundingClientRect().bottom - panel.clientTop;
  const height = bottom - top - divider.offsetHeight;
  const style = getComputedStyle(panel);
  const min = parseFloat(style.getPropertyValue('--spectrum-min'));
  const max = Math.max(min, height - parseFloat(style.getPropertyValue('--waterfall-min')));
  return {top, height, min, max};
}

export function PanelDivider({panel, plotWindow, setShare}) {
  const divider = useRef(null);
  const drag = useRef(null);
  const [resizing, setResizing] = useState(false);
  const [percent, setPercent] = useState(null);

  useEffect(() => {
    const area = plotWindow.current;
    function measure() {
      const {height, min, max} = resizeBounds(panel.current, area, divider.current);
      if (height > 0) {
        const percentage = size => Math.round(100 * size / height);
        setPercent({min: percentage(min), max: percentage(max), now: percentage(area.offsetHeight)});
      }
    }
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(area);
    observer.observe(panel.current);
    return () => observer.disconnect();
  }, [panel, plotWindow]);

  function resize(size, {height, min, max}) {
    if (height > 0) setShare(clamp(clamp(size, min, max) / height, 0, 1));
  }

  function handlePointerDown(event) {
    if (!event.isPrimary || event.button !== 0 || drag.current) return;
    drag.current = {id: event.pointerId, grab: event.clientY - event.currentTarget.getBoundingClientRect().top};
    setResizing(true);
    event.currentTarget.setPointerCapture(event.pointerId);
  }

  function handlePointerMove(event) {
    if (drag.current?.id !== event.pointerId) return;
    const bounds = resizeBounds(panel.current, plotWindow.current, event.currentTarget);
    resize(event.clientY - drag.current.grab - bounds.top, bounds);
  }

  function handleLostPointerCapture(event) {
    if (drag.current?.id !== event.pointerId) return;
    drag.current = null;
    setResizing(false);
  }

  function handleKeyDown(event) {
    const bounds = resizeBounds(panel.current, plotWindow.current, event.currentTarget);
    const size = plotWindow.current.offsetHeight;
    const positions = {ArrowUp: size - 16, ArrowDown: size + 16, Home: bounds.min, End: bounds.max};
    if (!Object.hasOwn(positions, event.key)) return;
    event.preventDefault();
    resize(positions[event.key], bounds);
  }

  return (
    <div
      id="capture-divider" ref={divider} className={resizing ? 'resizing' : undefined}
      role="separator" tabIndex={0} aria-orientation="horizontal" aria-controls="capture-window"
      aria-label="Spectrum height" aria-valuemin={percent?.min} aria-valuemax={percent?.max}
      aria-valuenow={percent?.now} title="Drag to resize the spectrum and waterfall"
      onPointerDown={handlePointerDown} onPointerMove={handlePointerMove}
      onLostPointerCapture={handleLostPointerCapture} onKeyDown={handleKeyDown}
    />
  );
}
