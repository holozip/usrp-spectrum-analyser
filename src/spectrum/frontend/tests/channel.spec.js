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
    await expect(panel.getByLabel('Demodulator').locator('option')).toHaveCount(7);
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
