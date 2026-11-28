import {lazy, Suspense} from 'react';
import {BroadcastDetails} from './BroadcastDetails.jsx';
import {fitsCapture, isAudioMode, MODES, settingsFromDraft, validSettings} from './channelSettings.js';
import {SidebarPanel} from './SidebarPanel.jsx';

const AcarsLog = lazy(() => import('./AcarsLog.jsx'));

function ChannelForm({draft, edit, pending, active, canStart, tune}) {
  const mode = draft.mode;
  const preset = MODES[mode];
  const fm = mode === 'nfm' || mode === 'wfm' || mode === 'wfm_stereo';
  const audio = isAudioMode(mode);

  function submit(event) {
    event.preventDefault();
    if (!active && canStart) void tune(settingsFromDraft(draft), audio);
  }

  return <form className="channel-form" onSubmit={submit} aria-label="Channel settings">
    <label>Frequency (MHz)<input name="frequency" type="number" min="70" max="6000" step="0.000001"
      required value={draft.frequency_text ?? draft.frequency_hz / 1e6}
      onChange={event => edit({frequency_text: event.target.value})} /></label>
    <label>Demodulator<select value={mode} onChange={event => edit({mode: event.target.value, modeExplicit: true,
      filter_hz: MODES[event.target.value].width, filter_text: undefined}, 0)}>
      {Object.entries(MODES).map(([value, item]) => <option key={value} value={value}>{item.label}</option>)}
    </select></label>
    <label>Filter (kHz)<input name="filter" type="number" min={preset.min / 1000} max={preset.max / 1000}
      step="0.001" required value={draft.filter_text ?? draft.filter_hz / 1000}
      onChange={event => edit({filter_text: event.target.value})} /></label>
    {audio && <label>Squelch (dBFS)<input name="squelch" type="number" min="-140" max="0" step="1" placeholder="Off"
      value={draft.squelch_dbfs ?? ''} onChange={event => edit({squelch_dbfs: event.target.value})} /></label>}
    {fm && <label>De-emphasis<select name="deemphasis" value={draft.deemphasis_us}
      onChange={event => edit({deemphasis_us: Number(event.target.value)}, 0)}>
      <option value="50">50 µs</option><option value="75">75 µs</option>
    </select></label>}
    {!active && <button type="submit" disabled={pending || !canStart}>
      {mode === 'acars' ? 'Start ACARS decoding' : mode === 'iq' ? 'Start IQ stream' : 'Listen'}
    </button>}
  </form>;
}

export function ChannelPanel({receiver, stop, close}) {
  const {active, state, capture, pending, error, configure, audio, volume, changeVolume, play, pause, draft, edit, selection} = receiver;
  const ready = validSettings(selection) && fitsCapture(selection, capture);
  const iq = active && state.settings.mode === 'iq';
  const acars = active && state.settings.mode === 'acars';
  const stereo = active && state.settings.mode === 'wfm_stereo';
  const broadcast = stereo || (active && state.settings.mode === 'wfm');
  const tuning = active && (pending || state.applied_revision !== state.revision);
  const power = tuning ? null : state?.power_dbfs;
  const snr = tuning ? null : state?.snr_db;
  const status = active && (!fitsCapture(state.settings, capture) ? 'Waiting for the selected channel in the IQ capture'
    : state.error ? 'Demodulation paused' : tuning ? 'Starting…' : iq ? 'Streaming IQ' : acars ? 'Decoding ACARS'
      : state.squelched ? 'Squelched' : audio || 'Audio paused');
  return <SidebarPanel id="channel-panel" label="Channel controls" title="Channel"
    closeLabel="Close channel controls" close={close}>
    <p>Click the spectrum or waterfall to select a channel.</p>
    <ChannelForm draft={draft} edit={edit} pending={pending} active={active} canStart={ready} tune={configure} />
    {!capture ? <p>{state?.capture ? 'Waiting for the updated spectrum capture.' : 'Zoom in until the dashboard shows Continuous capture.'}</p>
      : !ready && <p>The entire channel must fit in the current IQ capture.</p>}
    {active && <div className="channel-output">
      <span role="status">{status}</span>
      <div className="channel-signal" role="group" aria-label="Channel measurements">
        <dl>
          <div><dt>Power</dt><dd>{Number.isFinite(power) ? `${power.toFixed(1)} dBFS` : '—'}</dd></div>
          <div><dt>Est. SNR</dt><dd>{Number.isFinite(snr) ? `${snr.toFixed(1)} dB` : '—'}</dd></div>
        </dl>
        <meter aria-label="Channel signal level" min="-140" max="0"
          value={Math.max(-140, Math.min(0, power ?? -140))} />
      </div>
      {broadcast && <BroadcastDetails stereo={stereo} broadcast={state.broadcast} />}
      {iq ? <label>IQ stream URL<input readOnly value={new URL('/api/listen/iq', window.location.href).href}
        onFocus={event => event.target.select()} /><span>48 kS/s · complex float32</span></label> : !acars && <>
        <label>Volume<input type="range" min="0" max="1" step="0.01" value={volume}
          onChange={event => changeVolume(event.target.valueAsNumber)} /></label>
        {audio && audio !== 'Could not start browser audio'
          ? <button type="button" onClick={pause}>Pause audio</button>
          : <button type="button" disabled={pending} onClick={play}>Play audio</button>}
      </>}
      <button type="button" disabled={pending} onClick={stop}>
        {acars ? 'Stop ACARS decoding' : iq ? 'Stop IQ stream' : 'Stop listening'}
      </button>
    </div>}
    {(acars || (!active && draft.mode === 'acars')) && <Suspense fallback={<p>Loading ACARS messages…</p>}>
      <AcarsLog revision={state?.acars_revision} decoding={acars} />
    </Suspense>}
    {(error || state?.error) && <p className="input-error" role="alert">{error || state.error}</p>}
  </SidebarPanel>;
}
