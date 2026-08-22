import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';
import sveltePreprocess from 'svelte-preprocess';

export default defineConfig({
  plugins: [svelte({ preprocess: sveltePreprocess() })],
  server: {
    proxy: {
      // Use 127.0.0.1, not localhost — Node prefers ::1, uvicorn binds IPv4.
      '/api': 'http://127.0.0.1:8000',
      '/out': 'http://127.0.0.1:8000',
    },
  },
  build: {
    outDir: '../src/league_stats_api_ui/spa_dist',
    emptyOutDir: true,
  },
});
