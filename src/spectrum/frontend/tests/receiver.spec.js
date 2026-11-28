import {expect, test} from '@playwright/test';

// A browser-only fixture for the live API. The real demo cannot expose hardware controls.
async function mockReceiver(page, overrides = {}) {
  const state = {
    ready: true, focus: null, gain: 35, averages: 8, imageRequested: false,
    leg: 'A', continuous: false, offline: false, fail: null, receiverError: null,
    ...overrides,
  };
  const writes = [];
  const packets = new Map();
  let number = 0;

  await page.route('**/api/**', async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (request.method() === 'POST') {
      const setting = {'/api/focus': 'focus', '/api/gain': 'gain', '/api/averages': 'averages',
        '/api/image-reject': 'imageRequested'}[path];
      const value = request.postDataJSON();
      writes.push({setting, value});
      if (state.fail === setting) {
        state.fail = null;
        return route.fulfill({status: 503});
      }
      if (setting === 'focus' && state.holdFocus) await state.holdFocus;
      state[setting] = value;
      return route.fulfill({json: value});
    }
    if (path === '/api/gain-stages') {
      return route.fulfill({json: Array.from({length: 77}, (_, gain) => [2, 4, 1, gain % 20])});
    }
    if (path === '/api/sweep') {
      if (state.offline) return route.fulfill({status: 503});
      const peaks = Boolean(state.focus);
      const imageLeg = state.imageRequested && !state.continuous ? state.leg : null;
      const imageSet = imageLeg === 'C';
      const [low, high] = state.focus ?? [70e6, 6e9];
      const power = {A: -70, B: -60, C: -50}[imageLeg] ?? -65;
      const trace = state.ready ? (state.trace ?? Array(32).fill(power)) : [];
      if (state.ready) {
        number++;
        const powers = [power];
        if (peaks) powers.push(power + 5);
        if (imageSet) {
          powers.push(-70, -70);
          if (peaks) powers.push(-65);
          powers.push(-60);
          if (peaks) powers.push(-55);
        }
        const packet = Buffer.alloc(powers.length * trace.length * 4);
        for (const [index, level] of powers.entries()) {
          for (let bin = 0; bin < trace.length; bin++) {
            packet.writeFloatLE(index === 0 ? trace[bin] ?? -Infinity : level, (index * trace.length + bin) * 4);
          }
        }
        packets.set(number, packet);
      }
      return route.fulfill({json: {
        demo: false, fpga: true, number, progress: state.ready ? 1 : 0.2, trace,
        error: state.receiverError, focus: state.focus, capture_focus: state.focus,
        low, high, detail_count: trace.length, peaks, seconds: 0.08, hops: 5,
        gain_table: 'unified', gain: state.gain, requested_gain: state.gain,
        fft_averages: state.averages, requested_averages: state.averages,
        image_reject_available: true, image_reject_requested: state.imageRequested, image_leg: imageLeg,
        image_set: imageSet, continuous: state.continuous, rate_hz: 14e6, fft_size: 32768, gaps: 2,
      }});
    }
    if (path.startsWith('/api/detail/')) {
      const packet = packets.get(Number(path.split('/').at(-1)));
      return route.fulfill({body: packet, contentType: 'application/octet-stream'});
    }
    return route.fulfill({status: 404});
  });

  return {state, writes};
}

test('live gain previews unified stages and FFT averages preserve invalid or failed edits', async ({page}) => {
  const {state, writes} = await mockReceiver(page);
  await page.goto('/');
  const gain = page.getByRole('slider', {name: /RX gain/});
  const averages = page.getByRole('spinbutton', {name: 'FFT averages'});
  await expect(page.locator('#gain-stages')).toContainText('LPF 15 (15 dB)');

  await gain.focus();
  await page.keyboard.down('ArrowRight');
  await expect(page.locator('#gain-value')).toHaveText('36 dB');
  await expect(page.locator('#gain-stages')).toContainText('LPF 16 (16 dB)');
  expect(writes).toEqual([]);
  await page.keyboard.up('ArrowRight');
  await expect.poll(() => state.gain).toBe(36);

  await averages.fill('33');
  await averages.press('Enter');
  await expect(averages).toHaveValue('33');
  await expect(averages).toHaveAttribute('aria-invalid', 'true');
  await expect(page.getByText('Enter a whole number from 1 to 32 for FFT averages.')).toBeVisible();
  expect(writes).toEqual([{setting: 'gain', value: 36}]);

  state.fail = 'averages';
  await averages.fill('16');
  await averages.press('Enter');
  await expect(page.getByText('Could not change FFT averages.', {exact: false})).toBeVisible();
  await expect(averages).toHaveValue('16');
  expect(state.averages).toBe(8);

  await averages.press('Enter');
  await expect(page.locator('#detail')).toContainText('16 averages');
  await expect(averages).toHaveValue('16');
  expect(writes).toEqual([
    {setting: 'gain', value: 36}, {setting: 'averages', value: 16}, {setting: 'averages', value: 16},
  ]);

  const slider = await gain.boundingBox();
  await page.mouse.move(slider.x + slider.width * 0.7, slider.y + slider.height / 2);
  await page.mouse.down();
  await page.mouse.move(slider.x + slider.width * 0.8, slider.y + slider.height + 18, {steps: 4});
  expect(writes).toHaveLength(3);
  await page.mouse.up();
  await expect.poll(() => state.gain).toBeGreaterThan(36);
  expect(writes).toHaveLength(4);
});

test('waterfall auto range fits both sliders to the displayed floor and peak', async ({page}) => {
  const {state, writes} = await mockReceiver(page, {ready: false});
  await page.goto('/');
  const autoRange = page.getByRole('button', {name: 'Auto range'});
  await expect(autoRange).toBeDisabled();

  state.trace = [null, -149, ...Array(8).fill(-99), ...Array(21).fill(-60), -24];
  state.ready = true;
  await expect(autoRange).toBeEnabled();
  const freeze = page.getByRole('button', {name: 'Freeze trace'});
  await freeze.click();
  const waterfall = page.locator('#waterfall');
  const before = await waterfall.evaluate(canvas => canvas.toDataURL());

  await autoRange.press('Enter');
  const lower = page.getByRole('slider', {name: 'Waterfall lower level'});
  const upper = page.getByRole('slider', {name: 'Waterfall upper level'});
  await expect(lower).toHaveValue('-102');
  await expect(upper).toHaveValue('-21');
  expect(await waterfall.evaluate(canvas => canvas.toDataURL())).not.toBe(before);

  await lower.press('ArrowRight');
  await expect(lower).toHaveValue('-101');
  expect(writes).toEqual([]);
});

test('image rejection waits for a complete set, switches sources, and yields to continuous capture', async ({page}) => {
  const {state, writes} = await mockReceiver(page, {focus: [100e6, 102e6]});
  await page.goto('/');
  const image = page.getByRole('button', {name: 'Image reject'});
  const source = page.getByRole('combobox', {name: 'Trace and waterfall source'});
  const peak = page.getByRole('button', {name: 'Peak hold'});
  const reading = page.locator('#reading');
  const detail = page.locator('#detail');
  const plot = page.locator('#plot');

  await expect(peak).toBeEnabled();
  state.fail = 'imageRequested';
  await image.click();
  await expect(detail).toContainText('Could not change image rejection.');
  await expect(image).toHaveAttribute('aria-pressed', 'false');

  await image.click();
  await expect(detail).toContainText('Waiting for three sweeps');
  await expect(peak).toBeDisabled();
  await expect(source).toBeHidden();
  state.leg = 'C';
  await expect(source).toBeVisible();
  await expect(detail).toContainText('One result per three sweeps');
  await plot.hover({position: {x: 340, y: 120}});
  await expect(reading).toContainText('MHz');
  await expect(reading).not.toContainText('dBFS');

  for (const label of ['Raw A', 'Raw B', 'Raw C']) {
    await source.selectOption({label});
    await expect(peak).toBeEnabled();
  }
  await expect(page.locator('#peak-key')).toBeVisible();
  await peak.click();
  await expect(page.locator('#peak-key')).toBeHidden();

  state.leg = 'B';
  await page.waitForResponse(response => response.url().endsWith('/api/sweep') && response.ok());
  await expect(source).toHaveValue('C');
  await expect(source).toBeVisible();
  state.continuous = true;
  await expect(source).toBeHidden();
  await expect(page.locator('#state')).toHaveText('B210 FPGA · Continuous capture');
  await expect(detail).toContainText('Fixed tuning');
  await expect(detail).toContainText('RX gaps 2');
  await expect(detail).toContainText('Image rejection available in swept views');
  expect(writes).toEqual([{setting: 'imageRequested', value: true}, {setting: 'imageRequested', value: true}]);
});

test('polling recovers from empty, disconnected and receiver-error states', async ({page}) => {
  const {state} = await mockReceiver(page, {ready: false});
  await page.goto('/');
  const status = page.locator('#state');
  const detail = page.locator('#detail');
  await expect(detail).toHaveText('Waiting for the first sweep');

  state.offline = true;
  await expect(status).toHaveText('Disconnected · Retrying');
  state.offline = false;
  state.receiverError = 'Receiver unavailable';
  await expect(status).toHaveText('B210 FPGA · Retrying');
  await expect(detail).toHaveText('Receiver unavailable');

  state.receiverError = null;
  state.ready = true;
  await expect(detail).toContainText('Full sweep');
  await expect(status).toHaveText('B210 FPGA · Sweeping');
});

test('focus requests serialize overlapping gestures and recover after a failed update', async ({page}) => {
  const {state, writes} = await mockReceiver(page);
  await page.goto('/');
  const plot = page.locator('#plot');
  await expect(page.locator('#detail')).toContainText('Full sweep');
  const hold = Promise.withResolvers();
  state.holdFocus = hold.promise;
  try {
    await plot.press('+');
    await expect.poll(() => writes.length).toBe(1);
    await plot.press('+');
    await expect(page.locator('#detail')).toContainText('Updating frequency range');
    await page.waitForResponse(response => response.url().endsWith('/api/sweep') && response.ok());
    expect(writes).toHaveLength(1);
  } finally {
    hold.resolve();
  }
  await expect.poll(() => writes.length).toBe(2);
  const first = writes[0].value;
  const second = writes[1].value;
  expect(second[1] - second[0]).toBeLessThan(first[1] - first[0]);
  await expect(page.locator('#detail')).toContainText('Focused sweep');

  state.fail = 'focus';
  const full = page.getByRole('button', {name: 'Full spectrum'});
  await full.click();
  await expect(page.locator('#detail')).toContainText('Could not update the receiver sweep range.');
  await full.click();
  await expect(page.locator('#detail')).toContainText('Full sweep');
});
