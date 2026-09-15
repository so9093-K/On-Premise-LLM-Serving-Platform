import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  base: '/admin/console/',
  plugins: [react()],
  build: {
    outDir: '../../src/ai_model_serving/static/control-plane',
    assetsDir: 'assets',
    emptyOutDir: true,
    manifest: 'asset-manifest.json',
    sourcemap: false,
    target: 'es2022',
  },
});
