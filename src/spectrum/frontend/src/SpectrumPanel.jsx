import {useMemo, useRef, useState} from 'react';
import {PanelDivider} from './PanelDivider.jsx';
import {SpectrumPlot} from './SpectrumPlot.jsx';
import {Waterfall} from './Waterfall.jsx';
import {selectSpectrumData, spectrumReadout} from './spectrumData.js';
import {useFrequencyGestures} from './useFrequencyGestures.js';

function CaptureTools({toolsRef, peakHold, setPeakHold, rejected, frozen, toggleFreeze, image}) {
  return (
    <div className="capture-tools" id="capture-tools" ref={toolsRef} role="group" aria-label="Capture tools">
      <button
        id="peak-hold" type="button" aria-pressed={peakHold} disabled={rejected}
        title={rejected ? 'Peak hold is available on raw traces' : 'Peak hold'}
        onClick={() => setPeakHold(!peakHold)}
      >
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 19h18M4 16l4-3 4-4 3 4 5-3M10 5h5" /></svg>
        <span>Peak hold</span>
      </button>
      <button
        id="freeze" type="button" aria-pressed={frozen} aria-label="Freeze trace"
        title={frozen ? 'Resume trace' : 'Freeze trace'} onClick={toggleFreeze}
      >
        <svg className="freeze-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M9 5v14m6-14v14" /></svg>
        <svg className="resume-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M9 5l10 7-10 7z" /></svg>
        <span id="freeze-label">{frozen ? 'Resume' : 'Freeze'}</span>
      </button>
      <button
        id="image-reject" type="button" aria-pressed={image.enabled} disabled={image.pending}
        hidden={!image.available} title="Compare the normal sweep with ±5 MHz tuning shifts"
        onClick={image.toggle}
      >
        Image reject
      </button>
    </div>
  );
}

export function SpectrumPanel({
  latest, shown, view, frozen, toggleFreeze, status, previewRange, commitRange, cancelFocusDelay, image,
}) {
  const panel = useRef(null);
  const plotWindow = useRef(null);
  const tools = useRef(null);
  const [share, setShare] = useState(null);
  const [peakHold, setPeakHold] = useState(true);
  const [source, setSource] = useState('rejected');
  const gestures = useFrequencyGestures({view, shown, previewRange, commitRange, cancelFocusDelay});
  const waitingForImage = !shown?.imageSet && !frozen && !shown?.continuous && image.enabled;
  const data = useMemo(() => selectSpectrumData(shown, view, source, waitingForImage, peakHold),
    [shown, view, source, waitingForImage, peakHold]);

  function toggleImage() {
    if (!image.enabled) setSource('rejected');
    image.toggle();
  }

  return (
    <section
      id="spectrum-panel" aria-label="Spectrum plot" ref={panel}
      style={share == null ? undefined : {'--spectrum-share': share + 'fr', '--waterfall-share': (1 - share) + 'fr'}}
    >
      <div className="bar">
        <span id="state" role="status">{status}</span>
        <span id="peak-key" hidden={!data.peaks}>Recent peaks</span>
        <select
          id="trace-source" aria-label="Trace and waterfall source" hidden={!shown?.imageSet}
          value={source} onChange={event => setSource(event.currentTarget.value)}
        >
          <option value="rejected">Image rejected</option>
          <option value="A">Raw A</option>
          <option value="B">Raw B</option>
          <option value="C">Raw C</option>
        </select>
        <span id="reading">{spectrumReadout(data, view, gestures.cursor)}</span>
      </div>
      <progress id="progress" max={1} value={latest?.progress ?? 0} aria-label="Current sweep progress" />
      <div id="capture-window" ref={plotWindow}>
        <CaptureTools
          toolsRef={tools} peakHold={peakHold} setPeakHold={setPeakHold} rejected={data.rejected}
          frozen={frozen} toggleFreeze={toggleFreeze} image={{...image, toggle: toggleImage}}
        />
        <SpectrumPlot toolsRef={tools} data={data} view={view} gestures={gestures} />
      </div>
      <PanelDivider panel={panel} plotWindow={plotWindow} setShare={setShare} />
      <Waterfall shown={shown} data={data} view={view} frozen={frozen} gestures={gestures} />
    </section>
  );
}
