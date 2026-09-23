import { defineConfig } from 'vite'
export default defineConfig({
  // Share the ignored repository-root .env with the frontend. Vite exposes
  // only VITE_-prefixed values, so backend credentials stay server-side.
  envDir: '..',
  resolve: { alias: { vue: 'vue/dist/vue.esm-bundler.js' } },
  server: { host: '127.0.0.1', port: 5173, strictPort: true }
})
