import {useState} from 'react';
import {ReceiverControls, useReceiverSetting} from './ReceiverControls.jsx';
import {SpectrumPanel} from './SpectrumPanel.jsx';
import {FULL_RANGE, sameRange} from './spectrumData.js';
import {useFocusRequest} from './useFocusRequest.js';
import {useSweep} from './useSweep.js';

function connectionStatus(latest, frozen, disconnected) {
  if (disconnected) return 'Disconnected · Retrying';
  if (!latest) return 'Connecting';
  const mode = latest.demo ? 'Demo' : latest.fpga ? 'B210 FPGA' : 'B210';
  if (latest.error) return mode + ' · Retrying';
  if (frozen) return mode + ' · Trace frozen';
  return mode + (latest.continuous ? ' · Continuous capture' : ' · Sweeping');
}

function captureDescription(shown) {
  if (!shown?.number) return 'Waiting for the first sweep';
  let description;
  if (shown.continuous) {
    const tuning = shown.full_window ? 'Full 56 MHz window · DC and filter edges visible' : 'Fixed tuning';
    description = tuning + ' · ' + (shown.rate_hz / 1e6) + ' MS/s · '
      + shown.fft_size.toLocaleString() + '-point FFT' + (shown.gaps ? ' · RX gaps ' + shown.gaps : '');
  } else {
    const focused = shown.low > FULL_RANGE[0] || shown.high < FULL_RANGE[1];
    description = (focused ? 'Focused sweep' : 'Full sweep') + ' · '
      + shown.hops + ' hops · ' + shown.seconds + ' s per pass';
  }
  const averages = shown.fft_averages;
  if (averages != null) description += ' · ' + averages + (averages === 1 ? ' average' : ' averages');
  return description;
}

function detailStatus({latest, shown, frozen, view}, gain, averages, imageEnabled) {
  if (!latest) return 'Each trace combines sequential receiver captures.';
  if (gain.pendingValue !== null || (!latest.demo && latest.gain != null
      && latest.gain !== Math.trunc(latest.requested_gain))) return 'Applying RX gain…';
  if (latest.number && !frozen && !sameRange(latest.capture_focus, view)) return 'Updating frequency range…';
  if (averages.pendingValue !== null || (latest.fft_averages != null
      && latest.fft_averages !== latest.requested_averages)) return 'Applying FFT averages…';

  let image = '';
  if (imageEnabled && latest.continuous) image = ' · Image rejection available in swept views';
  else if (imageEnabled && !shown?.imageSet) image = ' · Waiting for three sweeps';
  else if (shown?.imageSet) image = ' · One result per three sweeps';
  return captureDescription(shown) + image;
}

export default function Dashboard() {
  // Before the first local gesture, use the focus the Python receiver reports on connection.
  const [localView, previewRange] = useState(undefined);
  const sweep = useSweep(localView);
  const {latest, shown, view, frozen, disconnected, toggleFreeze, confirmSetting} = sweep;
  const focus = useFocusRequest();
  const gain = useReceiverSetting('gain', 'requested_gain', confirmSetting,
    'Could not change RX gain. Adjust the slider or press Enter to retry.');
  const averages = useReceiverSetting('averages', 'requested_averages', confirmSetting,
    'Could not change FFT averages. Press Enter or leave the field to retry.');
  const imageSetting = useReceiverSetting('image-reject', 'image_reject_requested', confirmSetting,
    'Could not change image rejection. Select Image reject to retry.');

  const imageEnabled = imageSetting.pendingValue ?? latest?.image_reject_requested ?? false;
  const image = {
    enabled: imageEnabled,
    available: Boolean(latest?.image_reject_available),
    pending: imageSetting.pendingValue !== null,
    toggle: () => void imageSetting.save(!imageEnabled),
  };
  const error = latest?.error || focus.error || gain.error || imageSetting.error;

  function commitRange(range, delay) {
    previewRange(range);
    focus.request(range, delay);
  }

  return (
    <main>
      <ReceiverControls
        latest={latest} gain={gain} averages={averages} canReset={view !== null || Boolean(focus.error)}
        resetRange={() => commitRange(null)}
      />
      <SpectrumPanel
        latest={latest} shown={shown} view={view} frozen={frozen} status={connectionStatus(latest, frozen, disconnected)}
        toggleFreeze={toggleFreeze} previewRange={previewRange} commitRange={commitRange}
        cancelFocusDelay={focus.cancelDelay} image={image}
      />
      <footer>
        <p id="detail">{error || detailStatus(sweep, gain, averages, imageEnabled)}</p>
        <span className="visually-hidden" role="status">{error}</span>
        <p>Scroll to zoom · Drag to pan · Shift-drag to select a range</p>
        <p>dBFS axis · Drag to pan · Scroll to scale · Double-click to reset</p>
        <span id="frequency-keys" hidden>Plus and minus to zoom, left and right arrows to pan, Home for full spectrum.</span>
        <span id="power-keys" hidden>Plus and minus to scale, up and down arrows to pan, Home to reset.</span>
      </footer>
    </main>
  );
}
