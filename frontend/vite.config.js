import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';

export default defineConfig({
  plugins: [svelte()],
  server: {
    proxy: {
      '/api': 'http://localhost:8000',
      '/out': 'http://localhost:8000',
    },
  },
  build: {
    outDir: '../src/league_stats/web/spa_dist',
    emptyOutDir: true,
  },
});
