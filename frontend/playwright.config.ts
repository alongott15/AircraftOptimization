import { defineConfig, devices } from '@playwright/test';

/**
 * E2E runs against its own backend (port 8001) with a frozen clock and a golden DB seeded at T0, plus a Vite
 * dev server (port 5174) proxying /api to it. The password below is an E2E-only test value for this app.
 */
export const E2E_PASSWORD = 'e2e-only-test-password';
const T0 = '2026-01-15T08:00:00Z';
const backendEnv = [
  `APP_SQLITE_PATH=../data/e2e.sqlite`,
  `APP_MODELS_DIR=../data/e2e-models`,
  `APP_FROZEN_NOW=${T0}`,
  `APP_DEMO_PASSWORD=${E2E_PASSWORD}`,
  `APP_CORS_ORIGINS=http://127.0.0.1:5174`,
  `APP_LOG_LEVEL=WARNING`,
  `APP_LOGIN_RATE_PER_MIN=200`,
].join(' ');

export default defineConfig({
  testDir: './e2e',
  timeout: 60_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: 'http://127.0.0.1:5174',
    locale: 'he-IL',
    timezoneId: 'Asia/Jerusalem',
    viewport: { width: 1440, height: 900 },
    trace: 'retain-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } } }],
  webServer: [
    {
      command: `cd ../backend && ${backendEnv} uv run python -m app.seed.generator --anchor ${T0} --seed 42 --out ../data/e2e.sqlite && ${backendEnv} uv run uvicorn app.main:app --host 127.0.0.1 --port 8001`,
      url: 'http://127.0.0.1:8001/api/v1/health',
      timeout: 120_000,
      reuseExistingServer: false,
    },
    {
      command: 'VITE_API_TARGET=http://127.0.0.1:8001 pnpm exec vite --host 127.0.0.1 --port 5174 --strictPort',
      url: 'http://127.0.0.1:5174',
      timeout: 120_000,
      reuseExistingServer: false,
    },
  ],
});
