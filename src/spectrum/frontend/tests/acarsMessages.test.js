import assert from 'node:assert/strict';
import test from 'node:test';
import {assembleAcarsMessages} from '../src/acarsMessages.js';

function block(id, text, fields) {
  return {id, text, aircraft: 'N123AB', label: 'H1', frequency_hz: 131825000,
    mode: '2', block_id: String(id % 10), received_at: new Date(id * 1000).toISOString(), more: false, ...fields};
}

const POSITION_START =
  'F18AAC0861#M1BPOSN53431W001594,TNT,154057,320,RIBEL,154325,ORSUM,M54,21679,1658/RP:DA:EGLL:AA:CYVR:R:09R:F:RIBEL..BOPU';
const POSITION_END = 'F18BAC0861#M1BT..MERYT6D9C';

test('ACARS joins continuations, folds retries and passes the complete payload to Airframes', () => {
  const first = block(8, POSITION_START, {more: true});
  const retry = block(9, first.text, {more: true, block_id: first.block_id});
  const ack = block(10, 'S16AAC0861', {label: '_␡'});
  const second = block(11, POSITION_END);
  const lateRetry = block(12, first.text, {more: true, block_id: first.block_id});
  const [report, control] = assembleAcarsMessages([lateRetry, second, ack, retry, first]);
  assert.equal(control.title, 'Link acknowledgment');
  assert.equal(report.title, 'Position Report');
  assert.equal(report.protocol, 'ARINC 702');
  assert.equal(report.checksumVerified, true);
  assert.equal(report.more, false);
  assert.ok(report.joinedPayload.endsWith('RIBEL..BOPUT..MERYT6D9C'));
  assert.deepEqual(report.parts, [first, second]);
  assert.deepEqual(report.receptions.map(({block, retry}) => [block.id, retry]),
    [[8, false], [9, true], [11, false], [12, true]]);
  const fields = Object.fromEntries(report.fields);
  assert.equal(fields['Aircraft Route'], 'RIBEL >> BOPUT >> MERYT');
  assert.equal(fields.Altitude, '32000 feet');
  assert.equal(fields['Fuel Remaining'], '165800');
});

test('ACARS keeps incomplete, expired, unrelated and uplink blocks separate', () => {
  const first = block(1, POSITION_START, {more: true});
  const unrelated = [
    block(2, 'F18CAC0861#M1Bmissing middle block'),
    block(3, 'F18BBA0001#M1Bdifferent flight'),
    block(4, POSITION_END, {aircraft: 'N234AB'}),
    block(5, POSITION_END, {frequency_hz: 131725000}),
    block(6, POSITION_END.replace('#M1B', '#M2B')),
    block(7, POSITION_END, {block_id: 'Z'}),
    block(662, POSITION_END),
  ];
  const groups = assembleAcarsMessages([first, ...unrelated].toReversed());
  assert.equal(groups.length, 8);
  assert.ok(groups.every(({fields, parts, joinedPayload}) => fields.length === 0 && parts.length === 1 && joinedPayload === null));
  assert.equal(groups.at(-1).more, true);
  assert.equal(groups[0].missingStart, true);
});

test('ACARS keeps applications separate when a continuation has an ambiguous or explicit sublabel', () => {
  const known = block(1, 'F18AAC0861#M1BREQPWI/WQ360:RIBEL', {more: true});
  const other = block(2, 'F18AAC0861#M2BREQPWI/WQ320:TNT', {more: true});
  const ambiguous = block(3, 'F18BAC0861.ASLIB');
  const [fragment, second, first] = assembleAcarsMessages([ambiguous, other, known]);
  assert.equal(fragment.missingStart, true);
  assert.equal(first.more, true);
  assert.equal(second.more, true);

  const unknownStart = block(4, 'F18AAC0861Unknown start', {more: true});
  const continuation = block(5, 'F18BAC0861#M1B.ASLIB/DQ360F426');
  const [joined, unknown] = assembleAcarsMessages([continuation, unknownStart, known]);
  assert.deepEqual(joined.parts, [known, continuation]);
  assert.deepEqual(unknown.parts, [unknownStart]);
  assert.equal(joined.title, 'Request for Pilot Weather Information');
  assert.equal(Object.fromEntries(joined.fields)['Aircraft Route'], 'RIBEL > ASLIB');
});

test('ACARS displays native Airframes fields and keeps a bad checksum as raw text', () => {
  const text = 'F10ABA028K#M1BPRG/TS161541,281126/DTKSFO,28R,195,260618,/PR6375,302,320,1732,,124,0,,,60,P30,P0,,,,,,,M37A44F';
  const [corrupt, report] = assembleAcarsMessages([block(2, text.replace('1732', '1733')), block(1, text)]);
  assert.equal(report.title, 'Progress Report');
  assert.equal(report.checksumVerified, true);
  assert.equal(Object.fromEntries(report.fields)['Fuel at Destination'], '19500');
  assert.equal(Object.fromEntries(report.fields)['Cruise Temperature (C)'], '-37 degrees');
  assert.equal(corrupt.title, 'ACARS message');
  assert.equal(Boolean(corrupt.checksumVerified), false);
  assert.deepEqual(corrupt.fields, []);
  assert.equal(corrupt.receptions[0].block.text, text.replace('1732', '1733'));
});

test('ACARS retains an unsupported flight report and multipart avionics text without inventing decoded fields', () => {
  const wilco = block(1, 'L03AAC0857/SOUCAYA.AT1.C-FIUR6312FE8E00ABDD', {label: 'BA'});
  const onboardStart = block(2, 'O73AAC0857#10BSB016BA_262100-282\nER155800 -04  30  9-28234002818000M07', {more: true});
  const onboardEnd = block(3, 'O73BAC0857#10B\n  60  69 493  710  640');
  const [onboard, cpdlc] = assembleAcarsMessages([onboardEnd, onboardStart, wilco]);
  assert.equal(cpdlc.title, 'ACARS message');
  assert.deepEqual(cpdlc.fields, []);
  assert.equal(cpdlc.receptions[0].block, wilco);
  assert.equal(onboard.title, 'ACARS message');
  assert.deepEqual(onboard.fields, []);
  assert.equal(onboard.joinedPayload, 'SB016BA_262100-282\nER155800 -04  30  9-28234002818000M07\n  60  69 493  710  640');
});

test('ACARS exposes partial native decode results alongside the undecoded original text', () => {
  const text = 'M33AFRRUK7155859,,1632, 114,N   .    MMMM.MMM';
  const [report] = assembleAcarsMessages([block(1, text, {label: '16'})]);
  assert.equal(report.title, 'Position Report');
  assert.equal(report.partialDecode, true);
  assert.deepEqual(report.fields, [['Message Timestamp', '15:58:59'], ['Estimated Time of Arrival', '16:32:00']]);
  assert.equal(report.receptions[0].block.text, text);
});

test('ACARS passes a joined payload and its downlink header to Airframes operator formats', () => {
  const first = block(1, 'L95AQF0073/KSFO.TI2/', {more: true});
  const last = block(2, 'L95BQF0073030KSFOAFF5C');
  const [pending] = assembleAcarsMessages([first]);
  assert.deepEqual(pending.fields, []);

  const [report] = assembleAcarsMessages([last, first]);
  assert.equal(report.title, 'ATIS Subscription');
  assert.equal(Object.fromEntries(report.fields)['ATIS Code'], '030');
  assert.equal(report.checksumVerified, false);
  assert.equal(report.joinedPayload, '/KSFO.TI2/030KSFOAFF5C');
  assert.deepEqual(report.parts, [first, last]);
});
