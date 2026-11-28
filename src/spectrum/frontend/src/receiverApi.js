import {decodeDetail} from './spectrumData.js';

async function fetchJson(path, options) {
  const response = await fetch(path, options);
  if (!response.ok) throw new Error('Receiver returned HTTP ' + response.status);
  return response.json();
}

export function fetchSweep(signal) {
  return fetchJson('/api/sweep', {signal});
}

export function fetchGainStages(signal) {
  return fetchJson('/api/gain-stages', {signal});
}

export function fetchAcarsLog(signal) {
  return fetchJson('/api/listen/acars', {signal});
}

export function writeReceiverSetting(setting, value, signal) {
  return fetchJson('/api/' + setting, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(value),
    signal,
  });
}

export async function writeChannelSetting(value) {
  const response = await fetch('/api/listen', {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(value),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'Channel request failed');
  return result;
}

/** A null result means the four-frame server cache already evicted this sweep. */
export async function fetchDetail(frame, signal) {
  const options = new URLSearchParams();
  if (frame.peaks) options.set('peaks', '1');
  if (frame.image_set) options.set('image', '1');
  const query = options.size ? '?' + options : '';
  const response = await fetch('/api/detail/' + frame.number + query, {signal});
  if (response.status === 404) return null;
  if (!response.ok) throw new Error('Spectrum detail returned HTTP ' + response.status);
  return decodeDetail(frame, await response.arrayBuffer());
}
