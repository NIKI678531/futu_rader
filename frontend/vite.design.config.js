// Serves ../design (the byte-exact Claude Design mirror) as a static site so the
// original .dc.html screens can be opened side by side with the React port.
import { join } from 'node:path'
import { onNetworkDrive, watch } from './vite.env.js'

/** @type {import('vite').UserConfig} */
export default {
  root: '../design',
  // The mirror has no package.json, so Vite's cache would land in `design/.vite` and
  // dirty a directory that has to stay byte-identical to the design project. Park it
  // next to the React port's cache instead. `defineConfig` is gone for the reason
  // vite.config.js explains: bare specifiers resolve unreliably from this share.
  cacheDir: join(import.meta.dirname, 'node_modules/.vite-design'),
  // See vite.env.js for what a network drive breaks and why these two settings fix it.
  resolve: { preserveSymlinks: onNetworkDrive },
  server: { port: 5174, watch },
}
