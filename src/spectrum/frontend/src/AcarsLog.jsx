import {useEffect, useState} from 'react';
import {assembleAcarsMessages} from './acarsMessages.js';
import {fetchAcarsLog} from './receiverApi.js';

function AcarsMessage({message, grouped}) {
  const {first, header, title, fields, joinedPayload, missingStart, more, parts, receptions, protocol, checksumVerified, partialDecode} = message;
  const receivedAt = receptions.at(-1).block.received_at;
  const received = new Date(receivedAt);
  const retries = receptions.length - parts.length;
  const Heading = grouped ? 'h5' : 'h4';
  return <li>
    <div className="acars-message-header">
      <time dateTime={receivedAt} title={received.toLocaleString()}>{received.toLocaleTimeString()}</time>
      <span>{(first.frequency_hz / 1e6).toFixed(4)} MHz</span>
      {first.aircraft && <strong>{first.aircraft}</strong>}
      {header?.flight && <strong>{header.flight}</strong>}
      <span>Label {first.label}</span>
    </div>
    <Heading className="acars-title">{title}</Heading>
    {header && <div className="acars-message-header">
      <span>Message {parts.length > 1 ? header.sequence : header.sequence + header.part}</span>
      {protocol && <span>{protocol}</span>}
      {parts.length > 1 && <span>{parts.length} blocks</span>}
      {retries > 0 && <span>{retries} {retries === 1 ? 'retry' : 'retries'}</span>}
    </div>}
    {missingStart && <p className="acars-message-status">Earlier block missing</p>}
    {more && <p className="acars-message-status">More blocks follow</p>}
    {checksumVerified && <p className="acars-checksum">Payload checksum verified</p>}
    {partialDecode && <p className="acars-message-header">Partial decode</p>}
    {fields.length > 0 && <dl className="acars-fields">
      {fields.map(([name, value], index) => <div key={`${name}-${index}`}><dt>{name}</dt><dd>{value}</dd></div>)}
    </dl>}
    <details className="acars-raw">
      <summary>Raw text</summary>
      {joinedPayload && <div className="acars-raw-block">
        <div className="acars-message-header"><strong>Joined payload</strong></div>
        <pre className="acars-message-text">{joinedPayload}</pre>
      </div>}
      {receptions.map(({block, part, retry}) => <div key={block.id} className="acars-raw-block">
        <div className="acars-message-header">
          {part && <strong>{part}</strong>}
          <span>Block ID {block.block_id || '—'}</span>
          {block.mode && <span>Mode {block.mode}
            {block.mode === '2' && /^\d$/.test(block.block_id) && ' · VHF Category A downlink'}</span>}
          <time dateTime={block.received_at}>{new Date(block.received_at).toLocaleTimeString()}</time>
          {retry && <span>Retry</span>}
        </div>
        <pre className="acars-message-text">{block.text || 'No message text'}</pre>
      </div>)}
    </details>
  </li>;
}

function MessageList({name, messages, grouped}) {
  return <ol aria-label={name}>
    {messages.map(message => <AcarsMessage key={message.first.id} message={message} grouped={grouped} />)}
  </ol>;
}

export default function AcarsLog({revision, decoding}) {
  const [messages, setMessages] = useState(null);
  const [failed, setFailed] = useState(false);
  const [search, setSearch] = useState('');
  const [label, setLabel] = useState('');
  const [groupByAircraft, setGroupByAircraft] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    const {signal} = controller;
    let retry;

    async function load() {
      try {
        const log = await fetchAcarsLog(signal);
        if (signal.aborted) return;
        setMessages(assembleAcarsMessages(log.messages));
        setFailed(false);
      } catch {
        if (signal.aborted) return;
        setFailed(true);
        retry = setTimeout(load, 1000);
      }
    }

    void load();
    return () => {
      controller.abort();
      clearTimeout(retry);
    };
  }, [revision]);

  const labels = [...new Set(messages?.map(message => message.first.label))].sort();
  const selectedLabel = labels.includes(label) ? label : '';
  const terms = search.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const filtered = messages?.filter(message => {
    if (selectedLabel && message.first.label !== selectedLabel) return false;
    if (!terms.length) return true;
    const {first, header, body, title, fields, protocol, receptions} = message;
    const text = [first.aircraft, first.label, header?.flight, header?.sequence, protocol, title, body,
      ...fields.flat(), ...receptions.map(({block}) => block.text)].join(' ').toLowerCase();
    return terms.every(term => text.includes(term));
  });

  return <div className="acars-log" aria-labelledby="acars-heading">
    <h3 id="acars-heading">ACARS messages</h3>
    {failed && <p role="alert">Could not load ACARS messages. Retrying…</p>}
    {messages?.length ? <>
      <div className="acars-toolbar">
        <label className="acars-search">Search messages
          <input type="search" value={search} onChange={event => setSearch(event.target.value)} />
        </label>
        <label>ACARS label
          <select value={selectedLabel} onChange={event => setLabel(event.target.value)}>
            <option value="">All labels</option>
            {labels.map(item => <option key={item} value={item}>{item}</option>)}
          </select>
        </label>
        <label>Group messages
          <select value={groupByAircraft ? 'aircraft' : 'time'}
            onChange={event => setGroupByAircraft(event.target.value === 'aircraft')}>
            <option value="time">Newest first</option>
            <option value="aircraft">By aircraft</option>
          </select>
        </label>
      </div>
      {filtered.length === 0 ? <p>No matching messages.</p> : groupByAircraft
        ? [...Map.groupBy(filtered, message => message.first.aircraft || 'Unknown aircraft')].map(([aircraft, group]) =>
          <section key={aircraft} className="acars-aircraft">
            <h4>{aircraft}</h4>
            <MessageList name={`ACARS messages for ${aircraft}`} messages={group} grouped />
          </section>)
        : <MessageList name="Received ACARS messages" messages={filtered} />}
    </> : !failed && <p>{messages === null ? 'Loading…'
      : decoding ? 'Waiting for a valid message.' : 'Start decoding to receive messages.'}</p>}
  </div>;
}
