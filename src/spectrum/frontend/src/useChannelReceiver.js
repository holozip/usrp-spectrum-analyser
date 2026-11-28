import {useEffect, useEffectEvent, useRef, useState} from 'react';
import {createChannelAudio} from './channelAudio.js';
import {defaultChannelSetting, fitsCapture, isAudioMode, MODES, settingsFromDraft, validSettings} from './channelSettings.js';
import {writeChannelSetting} from './receiverApi.js';
import {channelEdges, FULL_RANGE, sameRange} from './spectrumData.js';

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
