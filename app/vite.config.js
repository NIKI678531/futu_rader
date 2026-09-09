import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { onNetworkDrive, watch } from './vite.env.js'

// `design/` holds the byte-exact Claude Design mirror. The React port imports the
// data layer and design-system CSS straight out of it so there is exactly one copy
// of each and re-importing from Claude Design cannot silently fork them.
export default defineConfig({
  plugins: [react()],
  // See vite.env.js for what a network drive breaks and why these two settings fix it.
  resolve: { preserveSymlinks: onNetworkDrive },
  server: {
    port: 5173,
    fs: { allow: ['..'] },
    watch,
  },
})
