import fs from 'node:fs';
import { defineConfig, devices } from '@playwright/test';

/**
 * End-to-end tests: the BUILT app (npm run build first, or `npm run test:e2e`) against a small fake server
 * (tests/e2e/fakeBackend.mjs). They use the Chrome that is already installed instead of downloading a browser;
 * set CHROME_PATH to point at another one, or leave it and Playwright falls back to its own Chromium.
 */
const PORT = Number(process.env.E2E_PORT || 4173);
const CHROME_CANDIDATES = [process.env.CHROME_PATH, 'C:/Program Files/Google/Chrome/Application/chrome.exe', '/usr/bin/google-chrome', '/usr/bin/chromium'];
const executablePath = CHROME_CANDIDATES.find((p): p is string => !!p && fs.existsSync(p));

export default defineConfig({
  testDir: 'tests/e2e',
  testMatch: '**/*.spec.ts',
  fullyParallel: false, // one fake server with one memory: the flows take turns
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: [['list']],
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: 'retain-on-failure',
    launchOptions: executablePath ? { executablePath } : {},
  },
  projects: [{ name: 'chrome', use: { ...devices['Desktop Chrome'], viewport: { width: 1200, height: 800 } } }],
  webServer: {
    command: 'node tests/e2e/fakeBackend.mjs',
    url: `http://127.0.0.1:${PORT}/app-next/`,
    env: { E2E_PORT: String(PORT) },
    reuseExistingServer: !process.env.CI,
    timeout: 20_000,
  },
});
