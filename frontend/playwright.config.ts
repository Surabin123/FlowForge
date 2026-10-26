import path from 'node:path'
import { defineConfig } from '@playwright/test'

const python = process.platform === 'win32'
  ? path.resolve('..', '.venv', 'Scripts', 'python.exe')
  : 'python'

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  retries: 0,
  reporter: 'list',
  outputDir: './test-results',
  use: {
    baseURL: 'http://127.0.0.1:18731',
    browserName: 'chromium',
    channel: process.platform === 'win32' ? 'msedge' : undefined,
    trace: 'retain-on-failure',
  },
  webServer: {
    command: `"${python}" ../backend/tests/browser_server.py`,
    url: 'http://127.0.0.1:18731/api/health',
    reuseExistingServer: false,
    timeout: 30_000,
  },
})
