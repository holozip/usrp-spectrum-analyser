import {defineConfig} from '@playwright/test';

const port = 18742;
const origin = `http://127.0.0.1:${port}`;

export default defineConfig({
  testDir: './src/spectrum/frontend/tests',
  testMatch: '*.spec.js',
  // The real demo receiver has one shared focus, just like a live receiver.
  workers: 1,
  use: {
    baseURL: origin,
    trace: 'retain-on-failure',
    launchOptions: {executablePath: process.env.SPECTRUM_BROWSER_PATH || undefined},
  },
  webServer: {
    command: `python3 src/spectrum/server.py --demo --port ${port}`,
    url: origin,
    reuseExistingServer: false,
  },
});
