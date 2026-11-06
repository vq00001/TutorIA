import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.test.ts', 'src/**/*.test.tsx'],
    maxWorkers: 1,
    cache: false,
    coverage: {
      provider: 'v8',
      reportsDirectory: process.env.TEST_REPORTS_DIR ?? '/tmp/tutoria-vitest-coverage',
      reporter: ['text', 'json-summary'],
      include: ['src/services/http.ts', 'src/components/materials/MaterialList.tsx', 'src/components/AuthGate.tsx'],
    },
  },
})
