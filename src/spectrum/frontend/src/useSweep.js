import {useEffect, useEffectEvent, useReducer, useRef} from 'react';
import {fetchDetail, fetchSweep} from './receiverApi.js';
import {clamp, keepImageSet} from './spectrumData.js';

const initialState = {
  latest: null,
  displayable: null,
  frozenFrame: undefined,
  initialFocus: null,
  disconnected: false,
};

function updateSweep(state, action) {
  switch (action.type) {
    case 'frame': {
      const latest = {...action.frame};
      for (const field of action.preserve) latest[field] = state.latest[field];
      const previous = state.displayable;
      const sameCapture = previous && latest.number === previous.number && latest.levels === previous.levels;
      return {...state, latest, disconnected: false,
        initialFocus: state.latest ? state.initialFocus : latest.focus ?? null,
        displayable: sameCapture || keepImageSet(latest, previous) ? previous : latest};
    }
    case 'setting':
      return {...state, latest: {...state.latest, [action.field]: action.value}};
    case 'freeze':
      return {...state, frozenFrame: state.frozenFrame === undefined ? state.displayable : undefined};
    case 'disconnect':
      return {...state, disconnected: true};
    default:
      return state;
  }
}

export function useSweep(localView) {
  const [state, dispatch] = useReducer(updateSweep, initialState);
  const writes = useRef({});
  const view = localView === undefined ? state.initialFocus : localView;
  const frozen = state.frozenFrame !== undefined;

  const pollDelay = useEffectEvent(() => {
    if ((view || state.latest?.listen?.settings) && !frozen) {
      return clamp((state.latest?.seconds || 0.08) * 500, 40, 350);
    }
    return 350;
  });

  useEffect(() => {
    const controller = new AbortController();
    const {signal} = controller;
    let timer;
    let detailNumber;
    let details;
    let completeSet;

    async function poll() {
      // A GET that started before a POST completed must not undo its confirmed value.
      const previousWrites = {...writes.current};
      try {
        const metadata = await fetchSweep(signal);
        let frame = metadata;
        if (metadata.number && !keepImageSet(metadata, completeSet)) {
          if (metadata.number !== detailNumber) {
            const loaded = await fetchDetail(metadata, signal);
            if (!loaded) return;
            detailNumber = metadata.number;
            details = loaded;
          }
          frame = {...metadata, ...details};
          completeSet = frame.imageSet ? frame : null;
        }
        if (!signal.aborted) {
          const preserve = Object.keys(writes.current).filter(field => writes.current[field] !== previousWrites[field]);
          dispatch({type: 'frame', frame, preserve});
        }
      } catch {
        detailNumber = undefined;
        details = undefined;
        completeSet = undefined;
        if (!signal.aborted) dispatch({type: 'disconnect'});
      } finally {
        if (!signal.aborted) timer = setTimeout(poll, pollDelay());
      }
    }

    poll();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, []);

  function confirmSetting(field, value) {
    writes.current[field] = (writes.current[field] ?? 0) + 1;
    dispatch({type: 'setting', field, value});
  }

  return {
    latest: state.latest,
    shown: frozen ? state.frozenFrame : state.displayable,
    view,
    frozen,
    disconnected: state.disconnected,
    toggleFreeze: () => dispatch({type: 'freeze'}),
    confirmSetting,
  };
}
