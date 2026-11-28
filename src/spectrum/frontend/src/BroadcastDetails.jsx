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

export function BroadcastDetails({stereo, broadcast}) {
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
