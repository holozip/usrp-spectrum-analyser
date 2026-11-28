import {expect, test} from '@playwright/test';

async function stopReceiver(request) {
  const response = await request.post('/api/listen', {data: {stop: 'full'}});
  expect(response.ok()).toBe(true);
}

async function dominantTone(page, channel = 0) {
  return page.evaluate(index => {
    const meter = window.channelTestAnalysers?.[index];
    if (!meter) return 0;
    const bins = new Float32Array(meter.frequencyBinCount);
    meter.getFloatFrequencyData(bins);
    const peak = bins.reduce((best, power, index) => power > bins[best] ? index : best, 0);
    return bins[peak] > -75 ? 48000 * peak / meter.fftSize : 0;
  }, channel);
}

test('ACARS shows new messages and keeps the log visible after stopping', async ({page, request}) => {
  await stopReceiver(request);
  const focus = [131775000, 131875000];
  expect((await request.post('/api/focus', {data: focus})).ok()).toBe(true);
  await expect.poll(async () => (await (await request.get('/api/listen')).json()).capture?.focus).toEqual(focus);

  const messages = [];
  await page.route('**/api/listen/acars', route => messages.length ? route.fulfill({json: {messages}}) : route.continue());
  await page.route('**/api/sweep', async route => {
    const response = await route.fetch();
    const metadata = await response.json();
    metadata.listen.acars_revision = messages.length;
    await route.fulfill({response, json: metadata});
  });

  try {
    await page.goto('/');
    await page.getByRole('button', {name: 'Channel', exact: true}).click();
    const panel = page.getByRole('complementary', {name: 'Channel controls'});
    await panel.getByLabel('Demodulator').selectOption('acars');
    await expect(panel.getByLabel('Filter (kHz)')).toHaveValue('10');
    await panel.getByRole('button', {name: 'Start ACARS decoding'}).click();
    await expect(panel.getByRole('status')).toHaveText('Decoding ACARS');
    await expect(panel.getByText('Waiting for a valid message.')).toBeVisible();
    await expect(panel.getByRole('button', {name: 'Play audio'})).toHaveCount(0);
    for (const route of ['audio', 'iq']) {
      expect((await request.get('/api/listen/' + route)).status()).toBe(409);
    }

    messages.unshift({id: 1, received_at: '2026-11-28T15:15:00.000Z', frequency_hz: 131825000,
      aircraft: 'N123AB', label: 'Q0', text: '<b>Test message</b>', more: false});
    const log = panel.getByRole('list', {name: 'Received ACARS messages'});
    await expect(log.getByText('N123AB', {exact: true})).toBeVisible();
    await expect(log.getByText('<b>Test message</b>', {exact: true})).toBeHidden();
    await log.getByText('Raw text', {exact: true}).click();
    await expect(log.getByText('<b>Test message</b>', {exact: true})).toBeVisible();

    const report = {frequency_hz: 131825000, aircraft: 'N123AB', label: 'H1', mode: '2'};
    messages.unshift({...report, id: 2, received_at: '2026-11-28T15:15:10.000Z', block_id: '5', more: true,
      text: 'F18AAC0861#M1BPOSN53431W001594,TNT,154057,320,RIBEL,154325,ORSUM,M54,21679,1658/RP:DA:EGLL:AA:CYVR:R:09R:F:RIBEL..BOPU'});
    const position = log.getByRole('listitem').filter({hasText: 'AC0861'});
    await expect(position.getByText('More blocks follow')).toBeVisible();
    await position.getByText('Raw text', {exact: true}).click();
    await expect(position.locator('pre')).toHaveCount(1);
    messages.unshift({...report, id: 3, received_at: '2026-11-28T15:15:11.000Z', block_id: '6', more: false,
      text: 'F18BAC0861#M1BT..MERYT6D9C'});
    await expect(position).toHaveCount(1);
    await expect(position.getByRole('heading', {name: 'Position Report'})).toBeVisible();
    await expect(position.getByText('RIBEL >> BOPUT >> MERYT')).toBeVisible();
    await expect(position.getByText('32000 feet')).toBeVisible();
    await expect(position.getByText('Payload checksum verified')).toBeVisible();
    await expect(position.getByText('Joined payload')).toBeVisible();
    await expect(position.locator('pre')).toHaveCount(3);
    await expect(position.locator('pre').nth(0)).toContainText('RIBEL..BOPUT..MERYT');
    await expect(position.locator('pre').nth(2)).toHaveText('F18BAC0861#M1BT..MERYT6D9C');
    await expect(position.getByText('More blocks follow')).toHaveCount(0);

    const wilco = 'L03AAC0857/SOUCAYA.AT1.C-FIUR6312FE8E00ABDD';
    messages.unshift({...report, id: 4, label: 'BA', aircraft: 'C-FIUR',
      received_at: '2026-11-28T15:15:12.000Z', block_id: '7', more: false, text: wilco});
    const reply = log.getByRole('listitem').filter({hasText: 'AC0857'});
    await expect(reply.getByRole('heading', {name: 'ACARS message'})).toBeVisible();
    await expect(reply.getByText('Payload checksum verified')).toHaveCount(0);
    await reply.getByText('Raw text', {exact: true}).click();
    await expect(reply.locator('pre')).toHaveText(wilco);

    const avionics = {...report, aircraft: 'C-FIUR'};
    messages.unshift({...avionics, id: 5, received_at: '2026-11-28T15:57:48.000Z', block_id: '8', more: false,
      text: 'F14AAC0857#M1BPOSN53335W001569,TIMPO,155744,340,RIBEL,160118,ASLIB,M57,21876,1310'});
    messages.unshift({...avionics, id: 6, received_at: '2026-11-28T15:58:07.000Z', block_id: '9', more: true,
      text: 'O73AAC0857#10BSB016BA_262100-282  3388   20002\nER155800 -04  30  9-28234002818000M07'});
    messages.unshift({...avionics, id: 7, received_at: '2026-11-28T15:58:15.000Z', block_id: '0', more: false,
      text: 'O73BAC0857#10B\n  60  69 493  710  640    0    0000000000FA8'});
    const onboard = log.getByRole('listitem').filter({hasText: 'Message O73'});
    await expect(onboard.getByRole('heading', {name: 'ACARS message'})).toBeVisible();
    await onboard.getByText('Raw text', {exact: true}).click();
    await expect(onboard.getByText('Joined payload')).toBeVisible();
    await expect(onboard.locator('pre')).toHaveCount(3);
    await expect(onboard.locator('pre').nth(0)).toContainText('9-28234002818000M07');
    await expect(onboard.locator('pre').nth(1)).toContainText('O73AAC0857#10B');
    await expect(onboard.locator('pre').nth(2)).toContainText('O73BAC0857#10B');

    messages.unshift({...avionics, id: 8, label: 'BA', received_at: '2026-11-28T16:00:00.000Z',
      block_id: '1', more: false, text: wilco.replace('L03', 'L04').replace('ABDD', 'ABDC')});
    await expect(log.getByText('Label BA')).toHaveCount(2);

    const search = panel.getByRole('searchbox', {name: 'Search messages'});
    await search.fill('c-fiur sb016');
    await expect(log.getByRole('listitem')).toHaveCount(1);
    await expect(onboard).toBeVisible();
    await panel.getByLabel('ACARS label').selectOption('BA');
    await expect(panel.getByText('No matching messages.')).toBeVisible();
    await search.clear();
    await expect(log.getByRole('listitem')).toHaveCount(2);
    await panel.getByLabel('ACARS label').selectOption('');
    await panel.getByLabel('Group messages').selectOption('aircraft');
    await expect(panel.getByRole('list', {name: 'ACARS messages for C-FIUR'}).getByRole('listitem')).toHaveCount(4);
    const otherAircraft = panel.getByRole('list', {name: 'ACARS messages for N123AB'});
    await expect(otherAircraft.getByRole('listitem')).toHaveCount(2);
    await search.fill('test message');
    await expect(otherAircraft.getByRole('listitem')).toHaveCount(1);
    await panel.getByLabel('Group messages').selectOption('time');
    await search.clear();

    await panel.getByRole('button', {name: 'Stop ACARS decoding'}).click();
    await expect(panel.getByRole('button', {name: 'Start ACARS decoding'})).toBeVisible();
    await expect(log.getByText('Label Q0')).toBeVisible();
  } finally {
    await stopReceiver(request);
  }
});

test('channel selection and demodulation reuse the running IQ capture', async ({page, request}) => {
  await stopReceiver(request);
  const initial = {mode: 'wfm', frequency_hz: 98100000};
  const sweptTune = await request.post('/api/listen', {data: initial});
  expect(sweptTune.status()).toBe(409);
  await page.addInitScript(() => {
    const original = AudioNode.prototype.connect;
    AudioNode.prototype.connect = function (destination, ...options) {
      if (this instanceof AudioWorkletNode) {
        const splitter = this.context.createChannelSplitter(2);
        original.call(this, splitter);
        window.channelTestAnalysers = [0, 1].map(channel => {
          const analyser = this.context.createAnalyser();
          analyser.fftSize = 4096;
          analyser.smoothingTimeConstant = 0.1;
          original.call(splitter, analyser, channel);
          return analyser;
        });
      }
      return original.call(this, destination, ...options);
    };
  });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const listenState = async () => (await (await request.get('/api/listen')).json());
  const sweep = async () => (await (await request.get('/api/sweep')).json());

  try {
    await page.goto('/');
    const sidePanels = page.getByRole('group', {name: 'Side panels'});
    const open = sidePanels.getByRole('button', {name: 'Channel', exact: true});
    const bookmarks = sidePanels.getByRole('button', {name: 'Bookmarks', exact: true});
    await expect(open).toBeEnabled();
    await open.click();
    const panel = page.getByRole('complementary', {name: 'Channel controls'});
    await bookmarks.click();
    const saved = page.getByRole('complementary', {name: 'Spectrum bookmarks'});
    await expect(saved.getByRole('heading', {name: 'Bookmarks'})).toBeVisible();
    await expect(open).toHaveAttribute('aria-expanded', 'true');
    await expect(bookmarks).toHaveAttribute('aria-expanded', 'true');
    await panel.getByRole('button', {name: 'Close channel controls'}).click();
    await expect(open).toBeFocused();
    await expect(open).toHaveAttribute('aria-expanded', 'false');
    await expect(saved).toBeVisible();
    await saved.getByRole('button', {name: 'Close bookmarks'}).click();
    await open.click();
    await expect(panel.getByRole('button', {name: 'Listen', exact: true})).toBeDisabled();
    await expect(panel.getByText('Zoom in until the dashboard shows Continuous capture.')).toBeVisible();
    expect((await request.post('/api/focus', {data: [97800000, 99000000]})).ok()).toBe(true);
    await expect.poll(async () => (await listenState()).capture?.center_hz).toBe(98400000);
    await expect.poll(async () => (await sweep()).capture_focus).toEqual([97800000, 99000000]);
    await page.reload();
    await expect(page.locator('#state')).toHaveText('Demo · Continuous capture');
    await open.click();
    const originalCapture = (await listenState()).capture;
    const before = await sweep();
    const spectrum = page.locator('#plot');
    const surveyLabel = await spectrum.getAttribute('aria-label');
    await expect(panel.getByLabel('Frequency (MHz)')).toHaveValue('98.4');
    await expect(panel.getByLabel('Demodulator').locator('option')).toHaveCount(8);
    const plot = await spectrum.boundingBox();
    expect((await panel.boundingBox()).x).toBeGreaterThanOrEqual(plot.x + plot.width);
    await page.mouse.click(plot.x + 58 + (plot.width - 78) * 0.25, plot.y + plot.height / 2);
    await expect.poll(async () => Number(await panel.getByLabel('Frequency (MHz)').inputValue())).toBeCloseTo(98.1, 2);
    expect((await listenState()).settings).toBe(null);
    await expect(page.locator('.channel-waterfall-passband')).toBeVisible();
    await panel.getByRole('button', {name: 'Listen', exact: true}).click();
    await expect(panel.getByRole('status')).toHaveText('Playing audio');
    const measurements = panel.getByRole('group', {name: 'Channel measurements'});
    await expect(measurements).toContainText(/Power-?\d+\.\d dBFS/);
    await expect(measurements).toContainText(/Est\. SNR-?\d+\.\d dB/);
    await spectrum.hover({position: {x: plot.width / 2, y: plot.height / 2}});
    await expect(page.locator('#reading')).toContainText('MHz');
    await expect(page.locator('#reading')).not.toContainText('dBFS');
    await panel.getByRole('heading', {name: 'Channel'}).hover();
    await expect(measurements).toBeVisible();
    await expect.poll(() => dominantTone(page)).toBeCloseTo(996.09, 1);
    expect((await listenState()).capture).toEqual(originalCapture);
    expect((await sweep()).windows).toBeGreaterThan(before.windows);
    await expect(spectrum).toHaveAttribute('aria-label', surveyLabel);
    await expect(page.getByText('Waterfall · newest at top')).toBeVisible();

    const permitted = (await listenState()).settings;
    const rejected = await request.post('/api/listen', {data: {...permitted, frequency_hz: 433920000}});
    expect(rejected.status()).toBe(409);
    expect((await listenState()).settings).toEqual(permitted);
    const activePlot = await spectrum.boundingBox();
    await page.mouse.click(activePlot.x + 58 + (activePlot.width - 78) * 0.30, activePlot.y + activePlot.height / 2);
    await expect.poll(async () => (await listenState()).settings.frequency_hz).toBeGreaterThan(98110000);
    expect((await listenState()).capture).toEqual(originalCapture);

    await panel.getByLabel('Demodulator').selectOption('wfm_stereo');
    await panel.getByLabel('Frequency (MHz)').fill('98.6');
    const rds = panel.getByRole('group', {name: 'RDS', exact: true});
    await expect(panel.getByText('Stereo locked')).toBeVisible();
    await expect.poll(() => dominantTone(page)).toBeCloseTo(996.09, 1);
    await expect.poll(() => dominantTone(page, 1)).toBeCloseTo(1699.22, 1);
    await expect(rds.getByText('DEMO FM')).toBeVisible();
    await expect(rds.getByText('CAFE')).toBeVisible();
    const quality = rds.getByRole('group', {name: 'RDS reception quality'});
    await expect(quality.getByText('Last 5 s')).toBeVisible();
    await expect(quality.getByText('Passed')).toBeVisible();
    await expect(quality.getByText('Corrected')).toBeVisible();
    await expect(quality.getByText('Rejected')).toBeVisible();
    await expect(rds.getByText('Pop music')).toBeVisible();
    await expect(rds.getByText('Traffic service')).toBeVisible();
    await expect(rds.getByText('98.1 · 100.7 MHz')).toBeVisible();
    const fm = panel.getByRole('group', {name: 'Broadcast FM details'});
    await expect(fm.getByText('19 kHz pilot')).toBeVisible();
    await expect(fm.getByText('100.0%')).toBeVisible();
    const textCells = rds.locator('.rds-text-cell');
    await expect(textCells).toHaveCount(64);
    await expect(textCells.nth(16)).toHaveText('e');
    await expect(textCells.nth(6)).toHaveAttribute('data-state', 'received');
    await expect(textCells.nth(26)).toHaveAttribute('data-state', 'unused');
    await expect(rds.getByText('Stereo and RDS test signal')).toHaveCount(1);
    await expect(rds.getByText('26/26 characters')).toBeVisible();
    expect((await listenState()).capture).toEqual(originalCapture);

    const textField = (await listenState()).broadcast.rds.radiotext_field;
    const restart = page.waitForResponse(response => response.url().endsWith('/api/listen')
      && response.request().method() === 'POST');
    await panel.getByLabel('De-emphasis').selectOption('75');
    const restarted = await (await restart).json();
    expect(restarted.broadcast.rds).toMatchObject({locked: false, radiotext_field: textField});
    await expect(rds.getByText('Stereo and RDS test signal')).toHaveCount(1);
    await expect(panel.getByRole('status')).toHaveText('Playing audio');

    await panel.getByLabel('Frequency (MHz)').fill('98.1');
    await expect(panel.getByText('Mono fallback · waiting for stereo pilot')).toBeVisible();
    await expect(rds.getByText('DEMO FM')).toHaveCount(0);
    await expect.poll(() => dominantTone(page, 1)).toBeCloseTo(996.09, 1);

    await panel.getByLabel('Demodulator').selectOption('iq');
    await panel.getByLabel('Frequency (MHz)').fill('98.1');
    await expect(panel.getByRole('button', {name: 'Apply channel settings'})).toHaveCount(0);
    await expect.poll(async () => (await listenState()).settings.frequency_hz).toBe(98100000);
    await expect(panel.getByRole('status')).toHaveText('Streaming IQ');
    await expect(panel.getByLabel('IQ stream URL')).toHaveValue(/\/api\/listen\/iq$/);
    const iqFrame = await page.evaluate(async () => {
      const response = await fetch('/api/listen/iq');
      const reader = response.body.getReader();
      let data = new Uint8Array();
      try {
        while (data.length < 12) {
          const {value} = await reader.read();
          data = new Uint8Array([...data, ...value]);
        }
        const samples = new DataView(data.buffer).getUint32(8, true);
        while (data.length < 12 + samples * 8) {
          const {value} = await reader.read();
          data = new Uint8Array([...data, ...value]);
        }
        return samples;
      } finally {
        await reader.cancel();
      }
    });
    expect(iqFrame).toBeGreaterThan(0);
    expect((await listenState()).capture).toEqual(originalCapture);
    await panel.getByRole('button', {name: 'Stop IQ stream'}).click();
    await expect(panel.getByLabel('Frequency (MHz)')).toHaveValue('98.1');
    expect((await listenState()).capture).toEqual(originalCapture);

    expect((await request.post('/api/focus', {data: [433500000, 434300000]})).ok()).toBe(true);
    await expect.poll(async () => (await listenState()).capture?.center_hz).toBe(433900000);
    await page.reload();
    await open.click();
    await panel.getByLabel('Demodulator').selectOption('nfm');
    await panel.getByLabel('Frequency (MHz)').fill('433.92');
    await panel.getByRole('button', {name: 'Listen', exact: true}).click();
    await expect(panel.getByRole('status')).toHaveText('Playing audio');
    await expect.poll(() => dominantTone(page)).toBeCloseTo(703.125, 1);
    await panel.getByLabel('Squelch (dBFS)').fill('-10');
    await expect(panel.getByRole('status')).toHaveText('Squelched');
    await expect.poll(() => dominantTone(page)).toBe(0);
    await page.getByRole('button', {name: 'Full spectrum'}).click();
    await expect(page.locator('#state')).toHaveText('Demo · Sweeping');
    expect(errors).toEqual([]);
  } finally {
    await stopReceiver(request);
  }
});
