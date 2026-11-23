import {defineConfig} from 'vite';
import react from '@vitejs/plugin-react';

const receiver = process.env.SPECTRUM_API_URL || 'http://127.0.0.1:8000';

export default defineConfig({
  root: 'src/spectrum/frontend',
  appType: 'mpa',
  plugins: [react()],
  server: {proxy: {'/api': receiver}},
  build: {outDir: '../../../build/spectrum', emptyOutDir: true},
});
