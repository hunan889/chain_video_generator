import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './tests/e2e/playwright',
  timeout: 30000,
  expect: { timeout: 10000 },
  fullyParallel: false,
  retries: 1,
  reporter: [
    ['list'],
    ['html', { outputFolder: 'playwright-report', open: 'never' }],
    ['junit', { outputFile: 'playwright-results.xml' }],
  ],
  use: {
    baseURL: process.env.BASE_URL || 'http://170.106.36.6:20002',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    video: 'off',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
});
