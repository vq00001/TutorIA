import { defineConfig } from '@playwright/test'
import { readFileSync } from 'node:fs'

const spki=readFileSync('/runtime/server.spki','utf8').trim()
if(!/^[A-Za-z0-9+/=]{44}$/.test(spki)) throw new Error('Invalid ephemeral TLS pin')
export default defineConfig({
  testDir: '.',
  testMatch: '**/*.spec.ts',
  timeout: 180_000,
  expect: { timeout: 15_000 },
  workers: 1,
  fullyParallel: false,
  retries: 0,
  outputDir: '/tmp/playwright-results',
  reporter: [['./safe-reporter.ts']],
  use: {
    baseURL: 'https://web-test:8443',
    browserName: 'chromium',
    ignoreHTTPSErrors: false,
    viewport: { width: 1280, height: 800 },
    launchOptions: { args: ['--disable-dev-shm-usage', '--ignore-certificate-errors-spki-list='+spki] },
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
})
