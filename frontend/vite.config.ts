import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

const backend = process.env.QUORUM_BACKEND ?? 'http://localhost:8000';

const csp = [
  "default-src 'self'",
  "script-src 'self'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data:",
  "font-src 'self'",
  "connect-src 'self'",
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
].join('; ');

const mcp = process.env.QUORUM_MCP ?? backend;
const proxy = { '/api': { target: backend, changeOrigin: false }, '/mcp': { target: mcp, changeOrigin: false } };

export default defineConfig({
  plugins: [
    react(),
    {
      name: 'quorum-csp',
      apply: 'build',
      transformIndexHtml: (html) =>
        html.replace('<head>', `<head>\n    <meta http-equiv="Content-Security-Policy" content="${csp}" />\n    <meta name="referrer" content="no-referrer" />`),
    },
  ],
  build: {
    assetsInlineLimit: 0,
    rollupOptions: {
      output: {
        manualChunks: {
          react: ['react', 'react-dom'],
          cytoscape: ['cytoscape'],
          'console-state': ['zustand', '@tanstack/react-query', 'zod'],
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: proxy,
  },
  preview: {
    port: 5173,
    proxy: proxy,
  },
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts'],
    // the logic that is unit tested: API client, token estimator and packer, workspace helpers (components are covered by the browser QA)
    coverage: { provider: 'v8', include: ['src/api/**', 'src/context/**', 'src/workspace/**'], exclude: ['**/*.test.ts', 'src/workspace/useWs.ts', 'src/workspace/live.ts', 'src/workspace/store.ts', 'src/workspace/server.ts', 'src/api/events.ts', 'src/api/schemas.ts'], reporter: ['text-summary', 'text'], thresholds: { lines: 90, functions: 85 } },
  },
});
