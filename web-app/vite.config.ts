import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';
import { APP_BASE } from './app-base.ts';

const API = 'http://127.0.0.1:8767'; // a local Deskhand server (python scripts/run_local.py)

export default defineConfig({
  base: APP_BASE,
  plugins: [react()],
  build: { outDir: 'dist', sourcemap: false, target: 'es2022' },
  server: {
    port: 5173,
    proxy: { '/v1': API, '/agent': { target: API, ws: true } },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    css: false,
    include: ['src/**/*.test.{ts,tsx}', 'tests/**/*.test.{ts,tsx}'],
  },
});
