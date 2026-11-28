import {MessageDecoder} from '@airframes/acars-decoder';

// ARINC 618 gives the ground system 11 minutes to assemble a VHF downlink.
const ASSEMBLY_TIME_MS = 11 * 60 * 1000;
const decoder = new MessageDecoder();
const LINK_TITLES = new Map([['_␡', 'Link acknowledgment'], ['Q0', 'ACARS link test']]);

function readPart(block) {
  const match = /^\d$/.test(block.block_id) && /^([A-Z1-8]\d{2})([A-Z])([A-Z0-9]{2}[A-Z0-9 ]{4})/.exec(block.text);
  if (!match) return {block, header: null, payload: block.text};
  const [, sequence, part, flight] = match;
  const body = block.text.slice(match[0].length);
  const prefix = block.label === 'H1' ? /^#[A-Z0-9]{2}[AB]/.exec(body)?.[0] : undefined;
  return {block, header: {sequence, part, flight: flight.trimEnd()}, prefix,
    payload: body.slice(prefix?.length ?? 0), envelope: match[0] + (prefix ?? '')};
}

function decodePayload(input, envelope, complete) {
  const {label, text} = input;
  const title = LINK_TITLES.get(label);
  const fallback = {title: title ?? 'ACARS message', fields: []};
  if (title || !complete || !text.trim()) return fallback;

  let result = decoder.decode(input);
  // Some Airframes plugins expect the original downlink header in the text.
  if (!result.decoded && envelope) {
    result = decoder.decode({...input, text: envelope + text});
  }
  if (!result.decoded) return fallback;

  const arinc702 = result.decoder.name === 'arinc-702';
  return {
    title: result.formatted.description || fallback.title,
    protocol: arinc702 ? 'ARINC 702' : null,
    checksumVerified: arinc702 && Boolean(result.raw.checksum_algorithm),
    partialDecode: result.decoder.decodeLevel === 'partial',
    fields: result.formatted.items.filter(({value}) => value).map(({label, value}) => [label, value]),
  };
}

function describeMessage({parts, prefix, receptions}) {
  const [{block: first, header, envelope}] = parts;
  const missingStart = Boolean(header && header.part !== 'A');
  const more = parts.at(-1).block.more;
  const complete = !missingStart && !more;
  const body = parts.map(({payload}) => payload).join('');
  const decoded = decodePayload({label: first.label, sublabel: prefix?.slice(1, 3), text: body}, envelope, complete);
  return {first, header, parts: parts.map(({block}) => block), receptions, body, missingStart, more,
    joinedPayload: complete && parts.length > 1 ? body : null, ...decoded};
}

function isRetry(block, {block: previous}) {
  return block.block_id === previous.block_id && block.more === previous.more && block.text === previous.text;
}

export function assembleAcarsMessages(blocks) {
  const groups = [];
  const bySequence = new Map();
  // The receiver exposes the newest block first. Process over-the-air order.
  for (const block of blocks.toReversed()) {
    const incoming = readPart(block);
    const {header, prefix} = incoming;
    const identity = header && JSON.stringify([
      block.frequency_hz, block.aircraft, block.mode, block.label, header.flight, header.sequence,
    ]);
    const candidates = bySequence.get(identity) ?? [];
    const receivedAt = Date.parse(block.received_at);
    const compatible = candidates.filter(candidate => {
      const age = receivedAt - Date.parse(candidate.parts[0].block.received_at);
      return age >= 0 && age <= ASSEMBLY_TIME_MS &&
        (!candidate.prefix || !prefix || candidate.prefix === prefix);
    });
    const retryGroup = compatible.findLast(candidate => candidate.parts.some(previous => isRetry(block, previous)));
    let group = retryGroup;
    if (!group && header && header.part !== 'A') {
      const open = compatible.filter(candidate => {
        const previous = candidate.parts.at(-1);
        return previous.block.more && header.part.charCodeAt(0) === previous.header.part.charCodeAt(0) + 1;
      });
      // Prefer an exact source match. A missing prefix cannot resolve multiple
      // applications with the same flight, sequence, and expected part.
      const exact = prefix && open.findLast(candidate => candidate.prefix === prefix);
      group = exact || (open.length === 1 ? open[0] : null);
    }
    if (!group) {
      group = {parts: [], receptions: [], prefix};
      groups.push(group);
      if (identity) {
        candidates.push(group);
        bySequence.set(identity, candidates);
      }
    }

    group.receptions.push({block, retry: Boolean(retryGroup), part: header && header.sequence + header.part});
    if (retryGroup) continue;
    group.parts.push(incoming);
    group.prefix ??= prefix;
  }
  return groups.sort((a, b) => b.receptions.at(-1).block.id - a.receptions.at(-1).block.id).map(describeMessage);
}
