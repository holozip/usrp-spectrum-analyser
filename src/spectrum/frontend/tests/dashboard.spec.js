import {expect, test} from '@playwright/test';

function watchBrowserErrors(page) {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => {
    if (message.type() === 'warning' || message.type() === 'error') errors.push(message.text());
  });
  return errors;
}

function focusPost(page) {
  return page.waitForResponse(response => response.url().endsWith('/api/focus') && response.ok());
}

async function resetReceiver(request) {
  const response = await request.post('/api/focus', {data: 'null', headers: {'Content-Type': 'application/json'}});
  expect(response.ok()).toBe(true);
}

async function waterfallRow(waterfall) {
  return waterfall.evaluate(canvas => {
    const sample = document.createElement('canvas');
    sample.width = canvas.width;
    sample.height = 1;
    const context = sample.getContext('2d');
    context.drawImage(canvas, 0, 0, canvas.width, 1, 0, 0, canvas.width, 1);
    const data = context.getImageData(0, 0, canvas.width, 1).data;
    return {painted: data.some((value, index) => index % 4 === 3 && value > 0), pixels: Array.from(data)};
  });
}

test('the production dashboard plots the real demo and supports a full inspect-and-reset journey', async ({page, request}) => {
  await resetReceiver(request);
  const errors = watchBrowserErrors(page);
  await page.goto('/?view=demo');
  const detail = page.locator('#detail');
  const plot = page.locator('#plot');
  const waterfall = page.locator('#waterfall');
  const freeze = page.getByRole('button', {name: 'Freeze trace'});
  const full = page.getByRole('button', {name: 'Full spectrum'});

  await expect(page.locator('#state')).toHaveText('Demo · Sweeping');
  await expect(detail).toContainText('Full sweep');
  await expect(full).toBeDisabled();
  await expect(page.getByLabel('RX gain', {exact: true})).toHaveCount(0);
  await expect(page.getByLabel('FFT averages', {exact: true})).toHaveCount(0);
  await expect(page.getByRole('button', {name: 'Image reject'})).toHaveCount(0);
  await expect.poll(async () => (await waterfallRow(waterfall)).painted).toBe(true);

  await freeze.click();
  await expect(freeze).toHaveAttribute('aria-pressed', 'true');
  const frozenRow = await waterfallRow(waterfall);
  await page.waitForResponse(response => response.url().endsWith('/api/sweep') && response.ok());
  expect(await waterfallRow(waterfall)).toEqual(frozenRow);

  const upper = page.getByLabel('Waterfall upper level');
  await upper.press('Home');
  await expect(upper).toHaveValue('-114');
  expect((await waterfallRow(waterfall)).pixels).not.toEqual(frozenRow.pixels);
  await freeze.click();

  const bounds = await plot.boundingBox();
  await page.mouse.move(bounds.x + bounds.width / 2, bounds.y + 130);
  const zoomed = focusPost(page);
  await page.mouse.wheel(0, -220);
  const zoomRange = (await zoomed).request().postDataJSON();
  expect(zoomRange[1] - zoomRange[0]).toBeLessThan(5.93e9);
  await expect(detail).toContainText('Focused sweep');
  await page.mouse.move(bounds.x + bounds.width / 2 + 3, bounds.y + 130);
  await expect(page.locator('#reading')).toContainText('kHz/bin');

  const panned = focusPost(page);
  await page.mouse.down();
  await page.mouse.move(bounds.x + bounds.width / 2 + 80, bounds.y + 130, {steps: 5});
  await page.mouse.up();
  expect((await panned).request().postDataJSON()[0]).toBeLessThan(zoomRange[0]);

  const waterfallBounds = await waterfall.boundingBox();
  await page.mouse.move(waterfallBounds.x + waterfallBounds.width * 0.25, waterfallBounds.y + 10);
  const selected = focusPost(page);
  await page.keyboard.down('Shift');
  await page.mouse.down();
  await page.mouse.move(waterfallBounds.x + waterfallBounds.width * 0.55, waterfallBounds.y + 10, {steps: 6});
  await page.mouse.up();
  await page.keyboard.up('Shift');
  const selection = (await selected).request().postDataJSON();
  expect(selection[1] - selection[0]).toBeLessThan(zoomRange[1] - zoomRange[0]);

  const divider = page.getByRole('separator', {name: 'Spectrum height'});
  const originalHeight = Number(await divider.getAttribute('aria-valuenow'));
  const dividerBounds = await divider.boundingBox();
  await page.mouse.move(dividerBounds.x + dividerBounds.width / 2, dividerBounds.y + 6);
  await page.mouse.down();
  await page.mouse.move(dividerBounds.x + dividerBounds.width / 2, dividerBounds.y - 45, {steps: 4});
  await page.mouse.up();
  await expect.poll(async () => Number(await divider.getAttribute('aria-valuenow'))).toBeLessThan(originalHeight);

  await page.reload();
  await expect(full).toBeEnabled();
  await expect(detail).toContainText('Focused sweep');
  await full.click();
  await expect(detail).toContainText('Full sweep');
  await expect(full).toBeDisabled();
  expect(new URL(page.url()).search).toBe('?view=demo');
  expect(errors).toEqual([]);
});

test('the phone-width layout keeps both charts and their keyboard controls usable at 2x density', async ({browser, request}) => {
  await resetReceiver(request);
  const page = await browser.newPage({viewport: {width: 390, height: 844}, deviceScaleFactor: 2, hasTouch: true});
  try {
    const errors = watchBrowserErrors(page);
    await page.goto('http://127.0.0.1:18742/');
    await expect(page.locator('#detail')).toContainText('Full sweep');
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(390);
    const divider = page.getByRole('separator', {name: 'Spectrum height'});
    await divider.press('Home');
    await expect(divider).toBeFocused();
    await expect.poll(async () => (await divider.getAttribute('aria-valuenow')) ===
      (await divider.getAttribute('aria-valuemin'))).toBe(true);
    await expect.poll(() => page.locator('#waterfall').evaluate(canvas => {
      const {top, bottom} = canvas.parentElement.getBoundingClientRect();
      return canvas.height === Math.floor(bottom * devicePixelRatio) - Math.ceil(top * devicePixelRatio);
    })).toBe(true);

    const plot = page.locator('#plot');
    const zoomed = focusPost(page);
    await plot.press('+');
    await zoomed;
    await expect(page.locator('#detail')).toContainText('Focused sweep');
    await expect(plot).toBeFocused();
    const axis = page.locator('#power-axis');
    await axis.press('+');
    await expect(axis).toHaveAttribute('aria-label', 'Power axis, -108 to -12 dBFS');
    await axis.press('Home');
    await expect(axis).toHaveAttribute('aria-label', 'Power axis, -120 to 0 dBFS');
    await plot.press('Home');
    await expect(page.locator('#detail')).toContainText('Full sweep');
    expect(errors).toEqual([]);
  } finally {
    await page.close();
  }
});
