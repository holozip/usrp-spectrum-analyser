import {lazy, Suspense, useEffect, useEffectEvent, useRef, useState} from 'react';
import {createChannelAudio} from './channelAudio.js';
import {writeChannelSetting} from './receiverApi.js';
import {channelEdges, FULL_RANGE, sameRange} from './spectrumData.js';
import {SidebarPanel} from './SidebarPanel.jsx';

const AcarsLog = lazy(() => import('./AcarsLog.jsx'));

const MODES = {
  usb: {label: 'USB · Upper sideband', width: 2700, min: 500, max: 4000},
  lsb: {label: 'LSB · Lower sideband', width: 2700, min: 500, max: 4000},
  nfm: {label: 'NFM · Narrow FM', width: 12500, min: 8000, max: 30000},
  wfm: {label: 'WFM · Broadcast FM mono', width: 200000, min: 140000, max: 240000},
  wfm_stereo: {label: 'WFM Stereo · Broadcast FM', width: 220000, min: 160000, max: 300000},
  am: {label: 'AM · Amplitude modulation', width: 10000, min: 3000, max: 20000},
  acars: {label: 'ACARS · Aircraft data link', width: 10000, min: 8000, max: 20000},
  iq: {label: 'IQ · Complex samples', width: 25000, min: 1000, max: 40000},
};

function isAudioMode(mode) {
  return Boolean(mode && mode !== 'iq' && mode !== 'acars');
}

export function defaultChannelSetting(frequency, mode, bandwidth) {
  frequency ??= 98100000;
  mode ??= frequency >= 87500000 && frequency <= 108000000 ? 'wfm' : 'nfm';
  const preset = MODES[mode];
  const filter = Math.round(bandwidth);
  return {frequency_hz: Math.round(frequency), mode,
    filter_hz: filter >= preset.min && filter <= preset.max ? filter : preset.width,
    squelch_dbfs: null, deemphasis_us: 50};
}

export function bookmarkChannelSetting(bookmark) {
  const label = bookmark.modulation.toLowerCase().trim();
  const mode = ['nfm', 'fm-n', 'narrow fm'].includes(label) ? 'nfm'
    : ['wfm', 'fm-w', 'wide fm', 'broadcast fm', 'broadcast fm mono'].includes(label) ? 'wfm'
      : ['fm stereo', 'wfm stereo', 'broadcast fm stereo', 'wfm_st'].includes(label) ? 'wfm_stereo'
      : Object.hasOwn(MODES, label) ? label : undefined;
  return defaultChannelSetting(bookmark.frequency_hz, mode, bookmark.bandwidth_hz);
}

function settingsFromDraft(draft) {
  return {mode: draft.mode,
    frequency_hz: Math.round(Number(draft.frequency_text ?? draft.frequency_hz / 1e6) * 1e6),
    filter_hz: Math.round(Number(draft.filter_text ?? draft.filter_hz / 1000) * 1000),
    squelch_dbfs: !isAudioMode(draft.mode) || draft.squelch_dbfs == null || draft.squelch_dbfs === ''
      ? null : Number(draft.squelch_dbfs),
    deemphasis_us: draft.deemphasis_us};
}

function validSettings(settings) {
  const {min, max} = MODES[settings.mode];
  const [low, high] = channelEdges(settings);
  const squelch = settings.squelch_dbfs;
  return low >= FULL_RANGE[0] && high <= FULL_RANGE[1] && settings.filter_hz >= min && settings.filter_hz <= max
    && (squelch === null || (Number.isInteger(squelch) && squelch >= -140 && squelch <= 0));
}

export function useChannelReceiver(state, confirmSetting, surveyView, requestedFocus) {
  const player = useRef(null);
  const sending = useRef(false);
  const [open, setOpen] = useState(null);
  const [pending, setPending] = useState(false);
  const [volume, setVolume] = useState(0.35);
  const [audio, setAudio] = useState(null);
  const [error, setError] = useState(null);
  const [edits, setEdits] = useState(null);
  const [liveEdit, setLiveEdit] = useState(null);
  const [lastSelection, setLastSelection] = useState(null);
  const capture = state?.capture && sameRange(state.capture.focus, requestedFocus) ? state.capture : null;
  const active = Boolean(state?.settings);
  const isAudio = isAudioMode(state?.settings?.mode);
  const revision = state?.revision;
  const center = surveyView ? (surveyView[0] + surveyView[1]) / 2 : undefined;
  const initial = state?.settings ?? lastSelection ?? defaultChannelSetting(center);
  const overrides = edits?.revision === revision ? edits?.fields : undefined;
  const draft = (active && liveEdit?.draft) || {...initial, ...overrides};

  function edit(fields, delay = 250) {
    if (active) {
      setLiveEdit({draft: {...draft, ...fields}, delay});
      setError(null);
      return;
    }
    setEdits(previous => ({revision, fields: {...(previous?.revision === revision ? previous?.fields : null), ...fields}}));
  }

  function selectBookmark(settings, nextRevision) {
    const preset = {...settings, squelch_dbfs: draft.squelch_dbfs, deemphasis_us: draft.deemphasis_us};
    setLastSelection(preset);
    setLiveEdit(null);
    setEdits({revision: nextRevision, fields: {...preset, modeExplicit: true}});
    setError(null);
    setOpen(true);
  }

  useEffect(() => () => player.current?.close(), []);
  useEffect(() => {
    if (!active) setLiveEdit(null);
    if (!isAudio && !sending.current && player.current) {
      player.current.close();
      player.current = null;
      setAudio(null);
    }
  }, [active, isAudio]);

  function openAudio() {
    if (player.current && audio !== 'Could not start browser audio') return player.current;
    player.current?.close();
    player.current = null;
    try {
      player.current = createChannelAudio(volume, setAudio);
      setAudio('Buffering audio…');
    } catch {
      setAudio('Could not start browser audio');
    }
    return player.current;
  }

  function pauseAudio() {
    player.current?.close();
    player.current = null;
    setAudio(null);
  }

  async function configure(settings, playAudio = false) {
    if (sending.current) return false;
    sending.current = true;
    setPending(true);
    setError(null);
    const output = playAudio && isAudioMode(settings?.mode) ? openAudio() : player.current;
    output?.reset(Infinity);
    if (output) setAudio('Buffering audio…');
    try {
      const result = await writeChannelSetting(settings);
      confirmSetting('listen', result);
      if (result.settings) setLastSelection(result.settings);
      else setLiveEdit(null);
      if (isAudioMode(result.settings?.mode)) {
        output?.reset(result.audio_epoch);
        void output?.play();
      } else {
        pauseAudio();
      }
      return result;
    } catch (failure) {
      if (isAudio) {
        output?.reset(state.audio_epoch);
        void output?.play();
      } else {
        pauseAudio();
      }
      setError(failure.message);
      return false;
    } finally {
      sending.current = false;
      setPending(false);
    }
  }

  const applyLiveEdit = useEffectEvent(async editToApply => {
    const settings = settingsFromDraft(editToApply.draft);
    if (!validSettings(settings) || !fitsCapture(settings, capture)) return;
    const unchanged = Object.keys(settings).every(field => settings[field] === state.settings[field]);
    const result = unchanged && !state.error ? true : await configure(settings);
    setLiveEdit(current => current !== editToApply ? current : result ? null : {...current, failed: true});
  });

  const [captureLow, captureHigh] = capture?.window ?? [];
  useEffect(() => {
    if (!active || !liveEdit || liveEdit.failed || pending || captureLow == null) return;
    const timer = setTimeout(() => void applyLiveEdit(liveEdit), liveEdit.delay);
    return () => clearTimeout(timer);
  }, [active, liveEdit, pending, captureLow, captureHigh]);

  function tuneAt(frequency) {
    const picked = active || overrides?.modeExplicit ? draft : defaultChannelSetting(frequency);
    const changedMode = picked.mode !== draft.mode;
    const width = changedMode ? picked.filter_hz : settingsFromDraft(draft).filter_hz || MODES[picked.mode].width;
    const [left, right] = channelEdges({mode: picked.mode, frequency_hz: 0, filter_hz: width});
    const step = picked.mode === 'usb' || picked.mode === 'lsb' ? 10 : 100;
    const hz = Math.max(FULL_RANGE[0] - Math.floor(left),
      Math.min(FULL_RANGE[1] - Math.ceil(right), Math.round(frequency / step) * step));
    if (active) {
      const next = {...settingsFromDraft(draft), frequency_hz: hz};
      if (fitsCapture(next, capture)) edit({frequency_hz: hz, frequency_text: undefined}, 0);
      else setError('The entire channel must fit in the current IQ capture.');
    } else {
      const mode = changedMode ? {mode: picked.mode, filter_hz: picked.filter_hz, filter_text: undefined} : {};
      edit({...mode, frequency_hz: hz, frequency_text: undefined});
    }
  }

  const selection = settingsFromDraft(draft);
  const channel = state?.settings ?? selection;
  return {selection, open: open ?? active, setOpen, active, state, capture, pending, error, configure, tuneAt, volume, audio, draft, edit, channel,
    selectBookmark, cancelEdits: () => setLiveEdit(null),
    play: () => void openAudio()?.play(), pause: pauseAudio,
    changeVolume(value) {
      setVolume(value);
      player.current?.volume(value);
    }};
}

function fitsCapture(selection, capture) {
  if (!capture || !(selection.filter_hz > 0)) return false;
  const [low, high] = channelEdges(selection);
  return low >= capture.window[0] && high <= capture.window[1];
}

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

// European RDS programme types. Keep the received PTY code in the API.
const PROGRAMME_TYPES = [
  'Unspecified', 'News', 'Current affairs', 'Information', 'Sport', 'Education', 'Drama', 'Culture',
  'Science', 'Varied speech', 'Pop music', 'Rock music', 'Easy listening', 'Light classical',
  'Serious classical', 'Other music', 'Weather', 'Finance', 'Children’s programmes', 'Social affairs',
  'Religion', 'Phone-in', 'Travel', 'Leisure', 'Jazz', 'Country music', 'National music', 'Oldies',
  'Folk music', 'Documentary', 'Alarm test', 'Alarm',
];

function trafficLabel({tp, ta}) {
  if (tp == null) return null;
  if (tp) return ta ? 'Announcement active' : 'Traffic service';
  if (ta == null) return null;
  return ta ? 'Linked traffic service (EON)' : 'No traffic service';
}

function RadioTextField({field}) {
  const {cells, end} = field;
  const rowWidth = 16;
  const limit = end ?? cells.length;
  const message = cells.slice(0, limit);
  const received = message.filter(cell => cell != null).length;
  const partial = received < limit;
  const readable = message.map(cell => cell ?? ' ').join('').replace(/\s+/g, ' ').trim();
  return <div className="rds-radiotext">
    <dt className="rds-text-heading">
      <span>{partial ? 'RadioText (partial)' : 'RadioText'}</span>
      <span className="rds-text-progress">{received}/{limit} {end == null && partial ? 'slots' : 'characters'}</span>
    </dt>
    <dd>
      <span className="visually-hidden">{readable || 'No characters decoded'}</span>
      <div className="rds-text-field" aria-hidden="true">
        {Array.from({length: cells.length / rowWidth}, (_, row) => {
          const start = row * rowWidth;
          return <div className="rds-text-row" key={start}>
            <span className="rds-text-index">{String(start).padStart(2, '0')}</span>
            {cells.slice(start, start + rowWidth).map((character, column) => {
              const index = start + column;
              return <span className="rds-text-cell" key={index}
                data-state={index >= limit ? 'unused' : character == null ? 'missing' : 'received'}>
                {character}
              </span>;
            })}
          </div>;
        })}
      </div>
    </dd>
  </div>;
}

function RdsQuality({quality}) {
  if (!quality) return null;
  const {passed, corrected, rejected, window_seconds: seconds} = quality;
  const counts = [
    ['passed', 'Passed', passed],
    ['corrected', 'Corrected', corrected],
    ['rejected', 'Rejected', rejected],
  ];
  const total = passed + corrected + rejected;
  return <div className="rds-quality" role="group" aria-label="RDS reception quality">
    <div className="rds-quality-heading"><span>Block quality</span><span>Last {seconds} s</span></div>
    <div className="rds-quality-track" aria-hidden="true">
      {counts.map(([state, , count]) => count > 0 &&
        <span key={state} data-state={state} style={{flex: count}} />)}
    </div>
    <ul className="rds-quality-values">
      {counts.map(([state, label, count]) => <li key={state} data-state={state}
        title={`${count} of ${total} blocks`}><strong>{Math.round(100 * count / total)}%</strong> {label}</li>)}
    </ul>
  </div>;
}

function BroadcastDetails({stereo, broadcast}) {
  const rds = broadcast?.rds;
  const rdsStatus = rds?.locked ? 'Decoded' : rds?.pi ? 'Reacquiring signal' : 'Waiting for data';
  const traffic = rds && trafficLabel(rds);
  const alternatives = rds?.alternative_frequencies_hz?.map(hz => (hz / 1e6).toFixed(1)).join(' · ');
  const stereoStatus = broadcast?.stereo_locked ? 'Stereo locked'
    : broadcast?.pilot_detected ? 'Mono fallback · waiting for stereo lock' : 'Mono fallback · waiting for stereo pilot';
  const percent = value => Number.isFinite(value) ? `${value.toFixed(1)}%` : '—';
  return <div className="broadcast-details" role="group" aria-label="Broadcast FM details">
    {stereo && <p className="broadcast-stereo" aria-live="polite">
      {stereoStatus}
    </p>}
    <dl className="fm-pilot-readings">
      <div><dt title="Pilot deviation as a share of the 75 kHz reference">19 kHz pilot</dt>
        <dd>{percent(broadcast?.pilot_level_percent)}</dd></div>
      <div><dt title="Gain applied to the stereo difference signal">Stereo blend</dt>
        <dd>{percent(broadcast?.stereo_blend_percent)}</dd></div>
    </dl>
    <div className="rds-details" role="group" aria-label="RDS">
      <div className="rds-heading"><strong>RDS</strong><span>{rdsStatus}</span></div>
      <RdsQuality quality={rds?.block_quality} />
      {rds?.pi && <dl>
        {rds.ps && <div><dt>Station</dt><dd>{rds.ps}</dd></div>}
        <div><dt>PI</dt><dd className="rds-identifier">{rds.pi}</dd></div>
        {rds.pty != null && <div><dt title={`European RDS PTY ${rds.pty}`}>Programme type</dt>
          <dd>{PROGRAMME_TYPES[rds.pty]}</dd></div>}
        {traffic && <div><dt>Traffic</dt><dd>{traffic}</dd></div>}
        {rds.alternative_frequencies_hz != null && <div className="rds-radiotext">
          <dt>Alternative frequencies</dt><dd>{alternatives ? `${alternatives} MHz` : 'None decoded'}</dd>
        </div>}
        {rds.radiotext_field ? <RadioTextField field={rds.radiotext_field} />
          : rds.radiotext && <div><dt>{rds.radiotext_partial ? 'RadioText (partial)' : 'RadioText'}</dt><dd>{rds.radiotext}</dd></div>}
      </dl>}
    </div>
  </div>;
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
