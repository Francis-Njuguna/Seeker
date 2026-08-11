import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    // The dev server runs in a container against a bind-mounted host folder.
    // inotify events do not cross that boundary on Docker Desktop (Windows and
    // macOS), so the default watcher never fires: Vite keeps serving the
    // transform it cached at startup and edits silently never reach the
    // browser. Polling is the documented escape hatch for exactly this setup.
    // 300ms is a deliberate compromise — fast enough to feel like a normal save
    // cycle, slow enough that stat()-ing the tree does not spin a core.
    watch: {
      usePolling: true,
      interval: 300,
    },
    // Proxy keeps the browser on one origin, so auth headers and image
    // requests work without any CORS handling in the client.
    proxy: {
      '/api': {
        target: process.env.VITE_API_PROXY || 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
