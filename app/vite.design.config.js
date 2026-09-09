// Serves ../design (the byte-exact Claude Design mirror) as a static site so the
// original .dc.html screens can be opened side by side with the React port.
import { defineConfig } from 'vite'

export default defineConfig({
  root: '../design',
  server: { port: 5174 },
})
