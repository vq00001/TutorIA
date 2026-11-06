import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

export default defineConfig({
  plugins: [react()],
  cacheDir: join(tmpdir(), 'tutoria-vite'),
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.test.ts', 'src/**/*.test.tsx'],
    maxWorkers: 1,
    cache: false,
    fsModuleCache: false,
    coverage: {
      provider: 'v8',
      reportsDirectory: process.env.TEST_REPORTS_DIR ?? join(tmpdir(), 'tutoria-vitest-coverage'),
      reporter: ['text', 'json-summary'],
      include: ['src/services/http.ts', 'src/components/materials/MaterialList.tsx', 'src/components/AuthGate.tsx'],
    },
  },
})
