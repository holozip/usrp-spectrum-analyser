import {expect, test} from '@playwright/test';

const makeBookmark = (id, title, frequency_hz = 101e6) => ({id, title, frequency_hz,
  bandwidth_hz: null, tag: '', modulation: 'FM', notes: 'Plain text <script>not executable</script>', created_at: '2026-11-27T00:00:00Z', updated_at: '2026-11-27T00:00:00Z'});

// Every API request is intercepted. These tests never tune a real receiver.
async function mockBookmarks(page, initial = []) {
  const state = {bookmarks: initial, focus: [100e6, 102e6], navigation: 8, revision: 1, fail: null, failLoad: false};
  const writes = [];
  let number = 0;
  await page.route('**/api/**', async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const method = request.method();
    if (path === '/api/sweep') return route.fulfill({json: {
      demo: true, number: ++number, trace: Array(32).fill(-65), detail_count: 32, peaks: false,
      low: state.focus?.[0] ?? 70e6, high: state.focus?.[1] ?? 6e9, focus: state.focus,
      capture_focus: state.focus, navigation_revision: state.navigation, progress: 1, seconds: .08, hops: 1,
      gain: 35, requested_gain: 35, fft_averages: 8, requested_averages: 8,
    }});
    if (path.startsWith('/api/detail/')) {
      const body = Buffer.alloc(32 * 4);
      for (let index = 0; index < 32; index++) body.writeFloatLE(-65, index * 4);
      return route.fulfill({body, contentType: 'application/octet-stream'});
    }
    if (path === '/api/gain-stages') return route.fulfill({json: []});
    if (path === '/api/focus') {
      const focus = request.postDataJSON();
      writes.push({path, body: focus});
      if (state.holdFocus) await state.holdFocus;
      state.focus = focus;
      return route.fulfill({json: focus});
    }
    if (path === '/api/bookmarks' && method === 'GET') {
      if (state.failLoad) return route.fulfill({status: 503, json: {error: 'Bookmark storage offline'}});
      return route.fulfill({json: {bookmarks: state.bookmarks, revision: state.revision}});
    }
    if (path.startsWith('/api/bookmarks') && method !== 'GET') {
      const body = method === 'DELETE' ? null : request.postDataJSON();
      writes.push({path, method, body});
      if (state.fail === method) {state.fail = null; return route.fulfill({status: 503, json: {error: 'Storage unavailable'}});}
      const id = path.split('/')[3];
      if (path.endsWith('/tune')) {
        const bookmark = state.bookmarks.find(item => item.id === id);
        const span = body.span_hz ?? (bookmark.bandwidth_hz == null ? 2e6 : Math.max(30000, Math.min(56e6, 3 * bookmark.bandwidth_hz)));
        state.focus = [bookmark.frequency_hz - span / 2, bookmark.frequency_hz + span / 2];
        return route.fulfill({json: {bookmark, focus: state.focus, navigation_revision: ++state.navigation}});
      }
      if (method === 'POST') {
        const bookmark = {...makeBookmark('added-' + state.revision++, body.title), ...body};
        state.bookmarks.push(bookmark);
        return route.fulfill({status: 201, json: bookmark});
      }
      if (method === 'PATCH') {
        const bookmark = state.bookmarks.find(item => item.id === id);
        Object.assign(bookmark, body);
        return route.fulfill({json: bookmark});
      }
      state.bookmarks = state.bookmarks.filter(item => item.id !== id);
      return route.fulfill({json: {deleted: id}});
    }
    return route.fulfill({status: 404, json: {error: 'Mock endpoint not found'}});
  });
  return {state, writes};
}

test('bookmark CRUD persists across reload, failures preserve edits, deletion is confirmed', async ({page}) => {
  const {state, writes} = await mockBookmarks(page);
  await page.goto('/');
  await page.getByRole('button', {name: 'Bookmarks', exact: true}).click();
  const panel = page.getByRole('complementary', {name: 'Spectrum bookmarks'});
  await panel.getByRole('button', {name: 'Add bookmark'}).click();
  await expect(panel.getByLabel('Frequency (MHz)')).toHaveValue('101');
  await panel.getByLabel('Title', {exact: true}).fill('Local FM');
  await panel.getByLabel('Modulation', {exact: true}).fill('WFM');
  await panel.getByLabel('Notes', {exact: true}).fill('Test <script>alert(1)</script>');
  state.fail = 'POST';
  await panel.getByRole('button', {name: 'Save bookmark'}).click();
  await expect(panel.getByRole('alert')).toContainText('Your changes are kept');
  await expect(panel.getByLabel('Title', {exact: true})).toHaveValue('Local FM');
  await panel.getByRole('button', {name: 'Save bookmark'}).click();
   await expect(panel.getByRole('rowheader', {name: 'Local FM', exact: true})).toBeVisible();
  await expect(panel.getByText('Test <script>alert(1)</script>', {exact: true})).toBeVisible();
  expect(state.bookmarks[0].frequency_hz).toBe(101e6);
  await page.reload();
  await page.getByRole('button', {name: 'Bookmarks', exact: true}).click();
  await panel.getByRole('button', {name: 'Edit Local FM', exact: true}).click();
  await panel.getByLabel('Title', {exact: true}).fill('Renamed FM');
  state.fail = 'PATCH';
  await panel.getByRole('button', {name: 'Save bookmark'}).click();
  await expect(panel.getByRole('alert')).toContainText('Storage unavailable');
  await expect(panel.getByLabel('Title', {exact: true})).toHaveValue('Renamed FM');
  await panel.getByRole('button', {name: 'Save bookmark'}).click();
  await panel.getByRole('button', {name: 'Delete Renamed FM', exact: true}).click();
  expect(state.bookmarks).toHaveLength(1);
  await panel.getByRole('button', {name: 'Keep bookmark'}).click();
  await panel.getByRole('button', {name: 'Delete Renamed FM', exact: true}).click();
  await panel.getByRole('button', {name: 'Confirm delete', exact: true}).click();
  await expect(panel.getByText('No bookmarks yet.', {exact: false})).toBeVisible();
  expect(writes.every(write => write.path.startsWith('/api/bookmarks'))).toBe(true);
});

test('tuning uses dedicated endpoint; external recalls sync once without undoing later gestures or freeze', async ({page}) => {
  const {state, writes} = await mockBookmarks(page, [makeBookmark('station', 'Remote station', 145e6)]);
  await page.goto('/');
  await expect(page.locator('#plot')).toHaveAttribute('aria-label', /100.0000 MHz to 102.0000 MHz/);
  await page.getByRole('button', {name: 'Bookmarks', exact: true}).click();
  await page.getByRole('button', {name: 'Tune to 145.0000 MHz, Remote station', exact: true}).click();
  await expect(page.locator('#plot')).toHaveAttribute('aria-label', /144.0000 MHz to 146.0000 MHz/);
  expect(writes).toEqual([{path: '/api/bookmarks/station/tune', method: 'POST', body: {span_hz: 2e6}}]);
  await page.getByRole('button', {name: 'Freeze trace'}).click();
  state.focus = [432e6, 434e6]; state.navigation++;
  await expect(page.locator('#plot')).toHaveAttribute('aria-label', /432.0000 MHz to 434.0000 MHz/);
  await expect(page.getByRole('button', {name: 'Freeze trace'})).toHaveAttribute('aria-pressed', 'true');
  await page.locator('#plot').press('+');
  await expect.poll(() => writes.filter(write => write.path === '/api/focus').length).toBe(1);
  const localLabel = await page.locator('#plot').getAttribute('aria-label');
  await page.waitForResponse(response => response.url().endsWith('/api/sweep'));
  await expect(page.locator('#plot')).toHaveAttribute('aria-label', localLabel);
  expect(state.navigation).toBe(10);
});

test('external bookmarks appear while open; flags follow visibility and crowded mobile labels stay disjoint', async ({page}) => {
  const {state} = await mockBookmarks(page, [makeBookmark('one', 'First station')]);
  await page.setViewportSize({width: 390, height: 844});
  await page.goto('/');
  await expect(page.locator('.bookmark-flags')).toHaveCount(0);
  await page.getByRole('button', {name: 'Bookmarks', exact: true}).click();
  await expect(page.locator('.bookmark-flag')).toHaveCount(1);
  state.bookmarks = Array.from({length: 30}, (_, index) => makeBookmark(String(index), 'Cluster ' + index));
  state.bookmarks.push(makeBookmark('outside', 'Offscreen station', 200e6));
   await expect(page.getByRole('rowheader', {name: 'Cluster 29', exact: true})).toBeAttached();
  const flags = page.locator('.bookmark-flag');
  await expect(flags.first()).toContainText('bookmarks');
  const boxes = await flags.evaluateAll(elements => elements.map(element => {
    const {left, top, right, bottom} = element.getBoundingClientRect();
    return {left, top, right, bottom};
  }));
  for (let index = 0; index < boxes.length; index++) {
    const a = boxes[index];
    expect(a.left).toBeGreaterThanOrEqual(0); expect(a.right).toBeLessThanOrEqual(390);
    for (const b of boxes.slice(index + 1)) expect(a.right <= b.left || b.right <= a.left || a.bottom <= b.top || b.bottom <= a.top).toBe(true);
  }
  await flags.first().click();
  await expect(page.getByText('Selected plot group', {exact: true})).toBeVisible();
   await expect(page.getByRole('rowheader', {name: 'Offscreen station', exact: true})).toHaveCount(0);
  await page.getByRole('button', {name: 'Close bookmarks'}).click();
  await expect(page.locator('.bookmark-flags')).toHaveCount(0);
  await expect(page.getByRole('button', {name: 'Bookmarks', exact: true})).toBeFocused();
});

test('server revision reset allows new external recalls without repeating them over local gestures', async ({page}) => {
  const {state, writes} = await mockBookmarks(page);
  await page.goto('/');
  const plot = page.locator('#plot');
  await expect(plot).toHaveAttribute('aria-label', /100.0000 MHz to 102.0000 MHz/);
  await plot.press('+');
  await expect.poll(() => writes.length).toBe(1);
  await expect(page.locator('#detail')).not.toContainText('Updating frequency range');
  const localLabel = await plot.getAttribute('aria-label');
  state.navigation = 0;
  state.focus = [70e6, 72e6];
  // Wait for the zero-baseline poll to be rendered, not just requested.
  await expect(page.locator('#detail')).toContainText('Updating frequency range');
  await expect(plot).toHaveAttribute('aria-label', localLabel);
  state.navigation = 1;
  state.focus = [432e6, 434e6];
  await expect(plot).toHaveAttribute('aria-label', /432.0000 MHz to 434.0000 MHz/);
  await plot.press('+');
  await expect.poll(() => writes.length).toBe(2);
  const nextLocalLabel = await plot.getAttribute('aria-label');
  await page.waitForResponse(response => response.url().endsWith('/api/sweep'));
  await expect(plot).toHaveAttribute('aria-label', nextLocalLabel);
  expect(state.navigation).toBe(1);
});

test('a later local gesture cancels a bookmark recall waiting for an older focus write', async ({page}) => {
  const {state, writes} = await mockBookmarks(page, [makeBookmark('station', 'Remote station', 145e6)]);
  await page.goto('/');
  await expect(page.locator('#plot')).toHaveAttribute('aria-label', /100.0000 MHz to 102.0000 MHz/);
  await page.getByRole('button', {name: 'Bookmarks', exact: true}).click();
  const hold = Promise.withResolvers();
  state.holdFocus = hold.promise;
  try {
    await page.locator('#plot').press('+');
    await expect.poll(() => writes.length).toBe(1);
    await page.getByRole('button', {name: 'Tune to 145.0000 MHz, Remote station', exact: true}).click();
    await expect(page.getByRole('button', {name: 'Tune to 145.0000 MHz, Remote station', exact: true})).toBeDisabled();
    await page.locator('#plot').press('ArrowRight');
  } finally {hold.resolve();}
  await expect.poll(() => writes.filter(write => write.path === '/api/focus').length).toBe(2);
  await expect(page.getByRole('button', {name: 'Tune to 145.0000 MHz, Remote station', exact: true})).toBeEnabled();
  expect(writes.filter(write => write.path.endsWith('/tune'))).toEqual([]);
  expect(state.navigation).toBe(8);
  await expect.poll(() => state.focus).toEqual(writes[1].body);
});

test('bookmark load failure offers retry and recovers without reload', async ({page}) => {
  const {state, writes} = await mockBookmarks(page, [makeBookmark('station', 'Recovered station')]);
  state.failLoad = true;
  await page.goto('/');
  await page.getByRole('button', {name: 'Bookmarks', exact: true}).click();
  const panel = page.getByRole('complementary', {name: 'Spectrum bookmarks'});
  await expect(panel.getByRole('alert')).toContainText('Could not load bookmarks: Bookmark storage offline');
  state.failLoad = false;
  await panel.getByRole('button', {name: 'Retry loading', exact: true}).click();
   await expect(panel.getByRole('rowheader', {name: 'Recovered station', exact: true})).toBeVisible();
  await expect(panel.getByRole('alert')).toHaveCount(0);
  expect(writes).toEqual([]);
});

test('failed tune and delete are actionable and preserve the saved bookmark', async ({page}) => {
  const {state, writes} = await mockBookmarks(page, [makeBookmark('station', 'Remote station', 145e6)]);
  await page.goto('/');
  await expect(page.locator('#plot')).toHaveAttribute('aria-label', /100.0000 MHz to 102.0000 MHz/);
  await page.getByRole('button', {name: 'Bookmarks', exact: true}).click();
  const panel = page.getByRole('complementary', {name: 'Spectrum bookmarks'});
  const tune = panel.getByRole('button', {name: 'Tune to 145.0000 MHz, Remote station', exact: true});
  state.fail = 'POST';
  await tune.click();
  await expect(panel.getByRole('alert')).toContainText('Could not tune to bookmark: Storage unavailable. Try again.');
  await expect(page.locator('#plot')).toHaveAttribute('aria-label', /100.0000 MHz to 102.0000 MHz/);
  expect(state.focus).toEqual([100e6, 102e6]);
  await expect(tune).toBeEnabled();
  await tune.click();
  await expect(page.locator('#plot')).toHaveAttribute('aria-label', /144.0000 MHz to 146.0000 MHz/);
  await panel.getByRole('button', {name: 'Delete Remote station', exact: true}).click();
  state.fail = 'DELETE';
  await panel.getByRole('button', {name: 'Confirm delete', exact: true}).click();
  await expect(panel.getByRole('alert')).toContainText('Could not delete bookmark: Storage unavailable. Try again.');
  expect(state.bookmarks).toHaveLength(1);
   await expect(panel.getByRole('rowheader', {name: 'Remote station', exact: true})).toBeVisible();
  await panel.getByRole('button', {name: 'Confirm delete', exact: true}).click();
  await expect(panel.getByText('No bookmarks yet.', {exact: false})).toBeVisible();
  expect(writes.filter(write => write.path === '/api/focus')).toEqual([]);
});

test('frozen spectrum selection, independent handles and numeric fields share one persistent draft; only Tune recalls', async ({page}) => {
  const {state, writes} = await mockBookmarks(page);
  await page.goto('/');
  await expect(page.locator('#plot')).toHaveAttribute('aria-label', /100.0000 MHz to 102.0000 MHz/);
  await page.getByRole('button', {name: 'Freeze trace'}).click();
  await page.getByRole('button', {name: 'Bookmark signal', exact: true}).click();
  const panel = page.getByRole('complementary', {name: 'Spectrum bookmarks'});
  await panel.getByLabel('Title', {exact: true}).fill('Picked signal');
  await panel.getByLabel('Tag', {exact: true}).fill(' Local ');
  await panel.getByLabel('Notes', {exact: true}).fill('Keep this draft while moving handles');
  const selection = page.getByRole('group', {name: 'Select bookmark frequency and bandwidth', exact: true});
  const box = await selection.boundingBox();
  const y = box.y + box.height * .8;
  await page.mouse.move(box.x + box.width * .2, y);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width * .4, y, {steps: 8});
  await page.mouse.up();
  await expect.poll(async () => Number(await panel.getByLabel('Frequency (MHz)').inputValue())).toBeCloseTo(100.6, 5);
  await expect.poll(async () => Number(await panel.getByLabel('Bandwidth (kHz)').inputValue())).toBeCloseTo(400, 3);
  const upper = page.getByRole('slider', {name: 'Bookmark upper band edge', exact: true});
  const upperBox = await upper.boundingBox();
  await page.mouse.move(upperBox.x + upperBox.width / 2, y);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width * .5, y, {steps: 8});
  await page.mouse.up();
  await expect.poll(async () => Number(await panel.getByLabel('Bandwidth (kHz)').inputValue())).toBeCloseTo(600, 3);
  await expect(panel.getByLabel('Title', {exact: true})).toHaveValue('Picked signal');
  await expect(panel.getByLabel('Tag', {exact: true})).toHaveValue(' Local ');
  await panel.getByLabel('Frequency (MHz)').fill('101');
  await panel.getByLabel('Bandwidth (kHz)').fill('200');
  await expect(page.getByRole('slider', {name: 'Bookmark centre frequency', exact: true})).toHaveAttribute('aria-valuenow', '101000000');
  await expect(upper).toHaveAttribute('aria-valuenow', '101100000');
  expect(writes).toEqual([]);
  await panel.getByRole('button', {name: 'Save bookmark', exact: true}).click();
  await expect(selection).toHaveCount(0);
  await expect(panel.getByRole('table')).toContainText('200 kHz');
  await expect(panel.getByRole('table')).toContainText('Local');
  expect(state.bookmarks[0]).toMatchObject({frequency_hz: 101e6, bandwidth_hz: 200e3, tag: 'Local', notes: 'Keep this draft while moving handles'});
  expect(writes).toHaveLength(1);
  await panel.getByRole('button', {name: 'Tune to 101.0000 MHz, Picked signal', exact: true}).click();
  await expect(page.locator('#plot')).toHaveAttribute('aria-label', /100.7000 MHz to 101.3000 MHz/);
  expect(writes[1]).toMatchObject({path: '/api/bookmarks/added-1/tune', body: {}});
  expect(writes.filter(write => write.path === '/api/focus')).toEqual([]);
});

test('click selection and keyboard handles do not navigate; Escape restores normal Shift-drag zoom', async ({page}) => {
  const {writes} = await mockBookmarks(page);
  await page.goto('/');
  await expect(page.locator('#plot')).toHaveAttribute('aria-label', /100.0000 MHz to 102.0000 MHz/);
  await page.getByRole('button', {name: 'Bookmark signal', exact: true}).click();
  const selection = page.getByRole('group', {name: 'Select bookmark frequency and bandwidth', exact: true});
  const box = await selection.boundingBox();
  await selection.click({position: {x: box.width * .7, y: box.height * .9}});
  await expect.poll(async () => Number(await page.getByLabel('Frequency (MHz)').inputValue())).toBeCloseTo(101.4, 4);
  const centre = page.getByRole('slider', {name: 'Bookmark centre frequency', exact: true});
  await centre.focus();
  const original = Number(await centre.getAttribute('aria-valuenow'));
  await centre.press('ArrowRight');
  expect(Number(await centre.getAttribute('aria-valuenow'))).toBeGreaterThan(original);
  const lower = page.getByRole('slider', {name: 'Bookmark lower band edge', exact: true});
  const oldWidth = Number(await page.getByLabel('Bandwidth (kHz)').inputValue());
  await lower.press('Shift+ArrowLeft');
  expect(Number(await page.getByLabel('Bandwidth (kHz)').inputValue())).toBeGreaterThan(oldWidth);
  expect(writes).toEqual([]);
  await lower.press('Escape');
  await expect(selection).toHaveCount(0);
  await expect(page.getByRole('form', {name: 'New bookmark'})).toHaveCount(0);
  expect(writes).toEqual([]);
  const plot = await page.locator('#plot').boundingBox();
  await page.keyboard.down('Shift');
  await page.mouse.move(plot.x + 58 + (plot.width - 78) * .2, plot.y + plot.height * .9);
  await page.mouse.down();
  await page.mouse.move(plot.x + 58 + (plot.width - 78) * .7, plot.y + plot.height * .9, {steps: 6});
  await page.mouse.up();
  await page.keyboard.up('Shift');
  await expect.poll(() => writes.filter(write => write.path === '/api/focus').length).toBe(1);
});

test('manual add and editing preserve unknown bandwidth; cancellation clears the overlay and mobile table owns overflow', async ({page}) => {
  const {writes} = await mockBookmarks(page, [{...makeBookmark('station', 'Saved signal'), bandwidth_hz: 250e3, tag: 'Broadcast'}]);
  await page.setViewportSize({width: 390, height: 844});
  await page.goto('/');
  await page.getByRole('button', {name: 'Bookmarks', exact: true}).click();
  const panel = page.getByRole('complementary', {name: 'Spectrum bookmarks'});
  await expect(panel.getByRole('table')).toContainText('Broadcast');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await panel.getByRole('button', {name: 'Edit Saved signal', exact: true}).click();
  await expect(page.locator('.bookmark-pick-surface')).toHaveCount(1);
  await expect(panel.getByLabel('Bandwidth (kHz)')).toHaveValue('250');
  await panel.getByRole('button', {name: 'Cancel', exact: true}).click();
  await expect(page.locator('.bookmark-pick-surface')).toHaveCount(0);
  await panel.getByRole('button', {name: 'Add bookmark', exact: true}).click();
  await expect(panel.getByLabel('Bandwidth (kHz)')).toHaveValue('');
  await panel.getByRole('button', {name: 'Select on spectrum', exact: true}).click();
  await expect(page.locator('.bookmark-pick-surface')).toHaveCount(1);
  await panel.getByRole('button', {name: 'Cancel', exact: true}).click();
  await expect(page.locator('.bookmark-pick-surface')).toHaveCount(0);
  expect(writes).toEqual([]);
});

test.describe('touch bookmark selection', () => {
  test.use({hasTouch: true, viewport: {width: 390, height: 844}});
  test('a touch tap selects a displayed signal without tuning', async ({page}) => {
    const {writes} = await mockBookmarks(page);
    await page.goto('/');
    await expect(page.locator('#plot')).toHaveAttribute('aria-label', /100.0000 MHz to 102.0000 MHz/);
    await page.getByRole('button', {name: 'Bookmark signal', exact: true}).tap();
    const selection = page.getByRole('group', {name: 'Select bookmark frequency and bandwidth', exact: true});
    await selection.scrollIntoViewIfNeeded();
    const box = await selection.boundingBox();
    await page.touchscreen.tap(box.x + box.width * .75, box.y + box.height * .9);
    await expect.poll(async () => Number(await page.getByLabel('Frequency (MHz)').inputValue())).toBeCloseTo(101.5, 4);
    await expect(page.getByLabel('Bandwidth (kHz)')).toHaveValue('100');
    expect(writes).toEqual([]);
    await page.getByRole('button', {name: 'Cancel', exact: true}).tap();
    await expect(selection).toHaveCount(0);
  });
});
