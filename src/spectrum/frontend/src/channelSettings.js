import {channelEdges, FULL_RANGE} from './spectrumData.js';

export const MODES = {
  usb: {label: 'USB · Upper sideband', width: 2700, min: 500, max: 4000},
  lsb: {label: 'LSB · Lower sideband', width: 2700, min: 500, max: 4000},
  nfm: {label: 'NFM · Narrow FM', width: 12500, min: 8000, max: 30000},
  wfm: {label: 'WFM · Broadcast FM mono', width: 200000, min: 140000, max: 240000},
  wfm_stereo: {label: 'WFM Stereo · Broadcast FM', width: 220000, min: 160000, max: 300000},
  am: {label: 'AM · Amplitude modulation', width: 10000, min: 3000, max: 20000},
  acars: {label: 'ACARS · Aircraft data link', width: 10000, min: 8000, max: 20000},
  iq: {label: 'IQ · Complex samples', width: 25000, min: 1000, max: 40000},
};

export function isAudioMode(mode) {
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

export function settingsFromDraft(draft) {
  return {mode: draft.mode,
    frequency_hz: Math.round(Number(draft.frequency_text ?? draft.frequency_hz / 1e6) * 1e6),
    filter_hz: Math.round(Number(draft.filter_text ?? draft.filter_hz / 1000) * 1000),
    squelch_dbfs: !isAudioMode(draft.mode) || draft.squelch_dbfs == null || draft.squelch_dbfs === ''
      ? null : Number(draft.squelch_dbfs),
    deemphasis_us: draft.deemphasis_us};
}

export function validSettings(settings) {
  const {min, max} = MODES[settings.mode];
  const [low, high] = channelEdges(settings);
  const squelch = settings.squelch_dbfs;
  return low >= FULL_RANGE[0] && high <= FULL_RANGE[1] && settings.filter_hz >= min && settings.filter_hz <= max
    && (squelch === null || (Number.isInteger(squelch) && squelch >= -140 && squelch <= 0));
}


export function fitsCapture(selection, capture) {
  if (!capture || !(selection.filter_hz > 0)) return false;
  const [low, high] = channelEdges(selection);
  return low >= capture.window[0] && high <= capture.window[1];
}
