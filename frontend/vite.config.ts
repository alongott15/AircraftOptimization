/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { '/api': { target: process.env.VITE_API_TARGET ?? 'http://127.0.0.1:8000', changeOrigin: false } },
  },
  build: {
    // three.js is only reached through the lazy AircraftViewer import, so it lands in its own on-demand chunk.
    chunkSizeWarningLimit: 1400,
    rollupOptions: {
      output: {
        manualChunks: { mantine: ['@mantine/core', '@mantine/hooks', '@mantine/notifications'] },
      },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
    css: false,
  },
});
