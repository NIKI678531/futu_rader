// Serves ../design (the byte-exact Claude Design mirror) as a static site so the
// original .dc.html screens can be opened side by side with the React port.
import { defineConfig } from 'vite'
import { onNetworkDrive, watch } from './vite.env.js'

export default defineConfig({
  root: '../design',
  // See vite.env.js for what a network drive breaks and why these two settings fix it.
  resolve: { preserveSymlinks: onNetworkDrive },
  server: { port: 5174, watch },
})
