import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The compose stack publishes the API on localhost:8000; override with
// CARMA_API_ORIGIN when running the backend elsewhere.
const apiOrigin = process.env.CARMA_API_ORIGIN ?? 'http://localhost:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': {
        target: apiOrigin,
        changeOrigin: true,
      },
    },
  },
})
