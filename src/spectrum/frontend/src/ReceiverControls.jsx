import {useEffect, useState} from 'react';
import {fetchGainStages} from './receiverApi.js';

function GainStageReadout({gain, table}) {
  const [stages, setStages] = useState(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    if (table !== 'unified') return;
    const controller = new AbortController();
    const {signal} = controller;
    fetchGainStages(signal).then(rows => {
      if (signal.aborted) return;
      setStages(rows);
      setFailed(false);
    }).catch(() => { if (!signal.aborted) setFailed(true); });
    return () => controller.abort();
  }, [table]);

  if (table !== 'unified') return null;
  if (failed) return <div id="gain-stages" role="status">Gain-stage readout unavailable</div>;
  const values = stages?.[Math.trunc(gain)];
  if (!values) return null;

  const [lna, mixer, tia, lpf] = values;
  // AD9361 UG-570, tables 27–30. LNA and mixer gain vary with frequency.
  const nominal = [[3, 14, 17, 21][lna], mixer < 3 ? [0, 3, 9][mixer] : mixer + 11, [-6, 0][tia], lpf];
  return (
    <div
      id="gain-stages" aria-label="Unified gain-table stages, nominal gain at 2.3 GHz"
      title="Stage indices and nominal gains from AD9361 UG-570. Actual gain varies with frequency."
    >
      {['LNA', 'Mixer', 'TIA', 'LPF'].map((name, index) => (
        <span key={name}>{name} <output htmlFor="gain">{values[index]} ({nominal[index]} dB)</output></span>
      ))}
      <span>Nominal at 2.3 GHz</span>
    </div>
  );
}

const GAIN_KEYS = ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End', 'PageUp', 'PageDown', 'Enter'];

function OverloadStatus({flags, fpga}) {
  let state = 'unavailable';
  let label = 'Overload status unavailable';
  let title = fpga ? 'Waiting for a valid AD9361 detector reading.' : 'Requires a compatible FPGA sweep image.';
  if (flags != null) {
    title = 'AD9361 detector status for the active RX chain. Peak flags can latch until gain changes or the detectors reset.';
    state = 'clear';
    label = 'No RX overload detected';
    if (flags !== 0) {
      const stages = [];
      if (flags & 0x18) stages.push('front end');
      if (flags & 0x06) stages.push('ADC');
      if (flags & 0x01) stages.push('digital');
      const severe = Boolean(flags & 0x15);
      state = severe ? 'overload' : 'warning';
      label = `${severe ? 'RX overload' : 'RX level warning'} · ${stages.join(', ')}`;
    }
  }
  return <span id="rx-overload" role="status" data-state={state} title={title}>{label}</span>;
}

function GainControl({requested, table, setting, overload, fpga}) {
  const [draft, setDraft] = useState(null);
  const value = draft ?? requested;

  async function commit() {
    if (draft === null || draft === requested) {
      setDraft(null);
      return;
    }
    if (await setting.save(draft)) setDraft(null);
  }

  return (
    <>
      <div className="gain-row">
        <label id="gain-control">
          RX gain
          <input
            id="gain" type="range" min="0" max="76" step="1" value={value}
            disabled={setting.pendingValue !== null}
            aria-describedby={setting.error ? 'detail' : undefined}
            onChange={event => setDraft(event.currentTarget.valueAsNumber)}
            onPointerUp={commit} onBlur={commit}
            onKeyUp={event => { if (GAIN_KEYS.includes(event.key)) void commit(); }}
          />
          <output id="gain-value" htmlFor="gain" aria-live="off">{value} dB</output>
        </label>
        <OverloadStatus flags={overload} fpga={fpga} />
      </div>
      <GainStageReadout table={table} gain={value} />
    </>
  );
}

function AveragesControl({requested, setting}) {
  const [draft, setDraft] = useState(null);
  const [validationError, setValidationError] = useState(null);
  const error = validationError || setting.error;

  async function commit(input) {
    if (draft === null) return;
    if (!input.validity.valid) {
      setValidationError('Enter a whole number from 1 to 32 for FFT averages.');
      return;
    }
    setValidationError(null);
    if (input.valueAsNumber === requested || await setting.save(input.valueAsNumber)) setDraft(null);
  }

  return (
    <div>
      <label id="averages-control" title="Number of FFT blocks averaged per capture">
        FFT averages{' '}
        <input
          id="averages" type="number" min="1" max="32" step="1" required value={draft ?? requested}
          disabled={setting.pendingValue !== null}
          aria-invalid={validationError ? true : undefined} aria-describedby={error ? 'averages-error' : undefined}
          onChange={event => { setDraft(event.currentTarget.value); setValidationError(null); }}
          onBlur={event => void commit(event.currentTarget)}
          onKeyDown={event => {
            if (event.key !== 'Enter') return;
            event.preventDefault();
            void commit(event.currentTarget);
          }}
        />
      </label>
      {error && <p id="averages-error" className="input-error" role="status">{error}</p>}
    </div>
  );
}

export function antennaLabel(value) {
  const [chain, port] = value.split(':');
  return `RF ${chain} · ${port}`;
}

export function ReceiverControls({latest, disconnected, antenna, gain, averages, canReset, resetRange}) {
  return (
    <div className="receiver-controls">
      <header>
        <div>
          {latest?.requested_gain != null && (
            <GainControl requested={latest.requested_gain} table={latest.gain_table} setting={gain}
              overload={disconnected || latest.error ? null : latest.rx_overload} fpga={latest.fpga} />
          )}
        </div>
        <div className="view-controls">
          {latest?.antenna_options?.length > 0 && <label id="antenna-control">
            RX antenna{' '}
            <select id="rx-antenna" value={antenna.pendingValue ?? latest.requested_antenna}
              disabled={antenna.pendingValue !== null}
              aria-describedby={antenna.error || latest.antenna_error ? 'detail' : undefined}
              onChange={event => void antenna.save(event.currentTarget.value)}>
              {latest.antenna_options.map(value => <option key={value} value={value}>{antennaLabel(value)}</option>)}
            </select>
          </label>}
          {latest?.requested_averages != null && (
            <AveragesControl requested={latest.requested_averages} setting={averages} />
          )}
          <button id="full-spectrum" type="button" disabled={!canReset} onClick={resetRange}>Full spectrum</button>
        </div>
      </header>
    </div>
  );
}
