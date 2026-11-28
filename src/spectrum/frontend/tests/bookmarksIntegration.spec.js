import {expect, test} from '@playwright/test';

// This suite uses the test-only Python --demo server and in-memory database.
// Never point its baseURL at a live receiver.
const ownedIds = new Set();
async function expectAlignedFlags(page) {
  await expect.poll(() => page.evaluate(() => {
    const canvas = document.querySelector('#plot');
    const {width} = canvas.getBoundingClientRect();
    const overlay = document.querySelector('.bookmark-flags');
    const svg = overlay.querySelector('svg');
    const bounds = overlay.getBoundingClientRect();
    const flags = [...overlay.querySelectorAll('button')];
    const circles = [...svg.querySelectorAll('circle')];
    const expectedX = [101e6, 101.05e6, 101.1e6].map(hz => 58 + (hz - 100e6) / 2e6 * (width - 78));
    return Math.abs(Number(svg.getAttribute('width')) - width) < 1
      && flags.every(flag => {
        const box = flag.getBoundingClientRect();
        return box.left >= bounds.left && box.right <= bounds.right;
      })
      && circles.length === 3
      && circles.every((circle, index) => Math.abs(Number(circle.getAttribute('cx')) - expectedX[index]) < 1);
  })).toBe(true);
}
test.beforeEach(async ({baseURL}) => {
  expect(baseURL).toBe('http://127.0.0.1:18742');
});
test.afterEach(async ({playwright, baseURL}) => {
  // A fresh context keeps cleanup usable even if the test's request fixture times out.
  const cleanup = await playwright.request.newContext({baseURL});
  try {
    for (const id of ownedIds) await cleanup.delete('/api/bookmarks/' + id);
    ownedIds.clear();
    await cleanup.post('/api/listen', {data: {stop: 'full'}});
  } finally {await cleanup.dispose();}
});

test('HTTP-created bookmarks share UI edits, persistence, recall and deletion', async ({page, request}) => {
  await request.post('/api/focus', {data: [100e6, 102e6]});
  const created = await request.post('/api/bookmarks', {data: {
    frequency_hz: 101e6, bandwidth_hz: 150e3, tag: 'Demo', title: 'API integration signal', modulation: 'Broadcast FM', notes: 'Unverified demo observation',
  }});
  expect(created.status()).toBe(201);
  const bookmark = await created.json();
  ownedIds.add(bookmark.id);
  const path = '/api/bookmarks/' + bookmark.id;
    await page.goto('/');
    await expect(page.locator('#plot')).toHaveAttribute('aria-label', /100.0000 MHz to 102.0000 MHz/);
    await page.getByRole('button', {name: 'Bookmarks', exact: true}).click();
    const panel = page.getByRole('complementary', {name: 'Spectrum bookmarks'});
     await expect(panel.getByRole('rowheader', {name: bookmark.title, exact: true})).toBeVisible();
     await panel.getByRole('button', {name: 'Edit ' + bookmark.title, exact: true}).click();
     await expect(panel.getByLabel('Bandwidth (kHz)')).toHaveValue('150');
     await expect(panel.getByLabel('Tag', {exact: true})).toHaveValue('Demo');
     await panel.getByLabel('Bandwidth (kHz)').fill('200');
     await panel.getByLabel('Tag', {exact: true}).fill('Confirmed demo');
     await panel.getByLabel('Notes', {exact: true}).fill('Edited by the browser, stored on the server');
     await page.screenshot({path: 'test-results/bookmark-selection.png', fullPage: true});
    await panel.getByRole('button', {name: 'Save bookmark'}).click();
    await expect(panel.getByText('Edited by the browser, stored on the server', {exact: true})).toBeVisible();
     expect((await (await request.get(path)).json()).notes).toBe('Edited by the browser, stored on the server');
     expect(await (await request.get(path)).json()).toMatchObject({bandwidth_hz: 200e3, tag: 'Confirmed demo'});
    await page.reload();
    await page.getByRole('button', {name: 'Bookmarks', exact: true}).click();
     await expect(panel.getByText('Edited by the browser, stored on the server', {exact: true})).toBeVisible();
     await page.getByRole('group', {name: 'Visible spectrum bookmarks'})
       .getByRole('button', {name: /^Tune to API integration signal, 101\.0000 MHz/}).click();
     await expect(page.locator('#plot')).toHaveAttribute('aria-label', /100.7000 MHz to 101.3000 MHz/);
    const channel = page.getByRole('complementary', {name: 'Channel controls'});
    await expect(channel.getByLabel('Frequency (MHz)')).toHaveValue('101');
    await expect(channel.getByLabel('Demodulator')).toHaveValue('wfm');
    await expect(channel.getByLabel('Filter (kHz)')).toHaveValue('200');
    const listen = async () => (await (await request.get('/api/listen')).json());
    expect((await listen()).settings).toBe(null);
    await expect.poll(async () => (await listen()).capture?.focus).toEqual([100.7e6, 101.3e6]);
    await expect(channel.getByRole('button', {name: 'Listen', exact: true})).toBeEnabled();
    const started = await request.post('/api/listen', {data: {mode: 'iq', frequency_hz: 101100000}});
    expect(started.status()).toBe(200);
    await expect(channel.getByRole('status')).toHaveText('Streaming IQ');
    await panel.getByRole('button', {name: 'Close bookmarks'}).click();
    await channel.getByRole('button', {name: 'Close channel controls'}).click();
    const recalled = await request.post(path + '/tune', {data: {span_hz: 500e3}});
    expect(recalled.status()).toBe(200);
    await expect(page.locator('#plot')).toHaveAttribute('aria-label', /100.7500 MHz to 101.2500 MHz/);
    await expect(channel.getByLabel('Demodulator')).toHaveValue('wfm');
    await expect(channel.getByLabel('Frequency (MHz)')).toHaveValue('101');
    expect((await listen()).settings).toMatchObject({frequency_hz: 101e6, mode: 'wfm', filter_hz: 200e3});
    const sweep = await (await request.get('/api/sweep')).json();
    expect(sweep.focus).toEqual([100.75e6, 101.25e6]);
    expect(sweep.navigation_revision).toBe((await recalled.json()).navigation_revision);
    await page.getByRole('button', {name: 'Bookmarks', exact: true}).click();
    expect((await request.delete(path)).status()).toBe(200);
    ownedIds.delete(bookmark.id);
     await expect(panel.getByRole('rowheader', {name: bookmark.title, exact: true})).toHaveCount(0);
});

test('desktop and phone bookmark flags remain readable on the real demo', async ({page, request}) => {
  await request.post('/api/focus', {data: [100e6, 102e6]});
  for (const [title, frequency] of [['Demo FM candidate', 101e6], ['Nearby demo signal', 101.05e6], ['Demo beacon', 101.1e6]]) {
    const response = await request.post('/api/bookmarks', {data: {
      frequency_hz: frequency, bandwidth_hz: 100e3, tag: 'Demo', title, modulation: 'Unknown', notes: 'Simulated data — UI demonstration only',
    }});
    expect(response.status()).toBe(201);
    ownedIds.add((await response.json()).id);
  }
    await page.setViewportSize({width: 1440, height: 1000});
    await page.goto('/');
    await page.getByRole('button', {name: 'Bookmarks', exact: true}).click();
    await expect(page.locator('.bookmark-flag')).toHaveCount(3);
     await expectAlignedFlags(page);
     expect(await page.locator('.bookmark-table .bookmark-actions button').evaluateAll(buttons =>
       buttons.every(button => getComputedStyle(button).whiteSpace === 'nowrap'
         && button.scrollWidth <= button.clientWidth))).toBe(true);
     await page.screenshot({path: 'test-results/bookmarks-desktop.png', fullPage: true});
    await page.setViewportSize({width: 390, height: 844});
    await expect(page.getByRole('complementary', {name: 'Spectrum bookmarks'})).toBeVisible();
    await expectAlignedFlags(page);
    await page.screenshot({path: 'test-results/bookmarks-mobile.png', fullPage: true});
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    const boxes = await page.locator('.bookmark-flag').evaluateAll(elements => elements.map(element => {
      const {left, right, top, bottom} = element.getBoundingClientRect();
      return {left, right, top, bottom};
    }));
    for (const [index, first] of boxes.entries()) {
      for (const second of boxes.slice(index + 1)) {
        expect(first.right <= second.left || second.right <= first.left || first.bottom <= second.top || second.bottom <= first.top).toBe(true);
      }
    }
});
