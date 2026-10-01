/// <reference types="vitest/config" />
import { writeFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'

// The API this bundle is built against. /api/build-info reports the backend's version, and
// the app warns when the two disagree — the D24 "stale build" lesson applied to the frontend.
const API_VERSION = '0.27.0-d27'

function buildMeta(): Plugin {
  const builtAt = new Date().toISOString()
  const buildId = builtAt.replace(/[-:.TZ]/g, '').slice(0, 14)
  return {
    name: 'platform-build-meta',
    config: () => ({
      define: {
        __BUILD_ID__: JSON.stringify(buildId),
        __API_VERSION__: JSON.stringify(API_VERSION),
      },
    }),
    closeBundle() {
      writeFileSync(
        resolve(import.meta.dirname, 'dist', 'build-meta.json'),
        JSON.stringify({ build_id: buildId, built_at: builtAt, api_version: API_VERSION }, null, 2),
      )
    },
  }
}

// In development the React dev server proxies API calls to FastAPI, so the browser still
// sees a single origin and cookies behave exactly as they do in production.
const backend = process.env.PLATFORM_BACKEND ?? 'http://127.0.0.1:8000'

export default defineConfig({
  base: '/app/',
  plugins: [react(), buildMeta()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': backend,
      '/health': backend,
      '/ready': backend,
      '/ui': backend,
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    sourcemap: false,
    chunkSizeWarningLimit: 700,
    // Never inline fonts as data: URIs. The app's CSP is `font-src 'self'`, which blocks
    // data: fonts; small subsets would otherwise fall under Vite's 4 KB inline threshold.
    assetsInlineLimit: (file) => (/\.woff2?$/i.test(file) ? false : undefined),
  },
  test: {
    include: ['src/**/*.test.ts', 'src/**/*.test.tsx'],
    environment: 'node',
  },
})
