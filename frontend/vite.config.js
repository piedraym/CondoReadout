import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The API (FastAPI) runs on port 8000. While developing, the page asks Vite for /search and
// /report and Vite passes the request on, so the browser sees a single address.
const API = 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/search': API,
      '/report': API,
      '/health': API,
    },
  },
})
