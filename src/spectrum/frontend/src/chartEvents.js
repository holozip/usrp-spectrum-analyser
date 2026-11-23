import {useEffect, useEffectEvent} from 'react';
import {clamp} from './spectrumData.js';

export function wheelZoom(event) {
  const delta = event.deltaY * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? 120 : 1);
  return Math.exp(clamp(delta / 280, -2, 2));
}

// React delegates wheel events passively. Handled chart gestures need to cancel page scrolling.
export function useChartWheel(surfaceRef, onWheel) {
  const handleWheel = useEffectEvent(onWheel);
  useEffect(() => {
    const surface = surfaceRef.current;
    const listener = event => handleWheel(event);
    surface.addEventListener('wheel', listener, {passive: false});
    return () => surface.removeEventListener('wheel', listener);
  }, [surfaceRef]);
}

export function watchPixelRatio(onChange) {
  let query;
  function watch() {
    query = window.matchMedia(`(resolution: ${window.devicePixelRatio}dppx)`);
    query.addEventListener('change', handleChange, {once: true});
  }
  function handleChange() {
    watch();
    onChange();
  }
  watch();
  return () => query.removeEventListener('change', handleChange);
}
