import { createRequire } from 'node:module'
import { onNetworkDrive, watch } from './vite.env.js'

/* Both imports above are deliberate: `node:module` is a builtin and `./vite.env.js` is
   relative, so neither goes through package resolution. Bare specifiers here do, and on
   this DFS share that resolution fails intermittently — Vite bundles the config with
   `preserveSymlinks: false` hardcoded, so it realpaths every package to the UNC share
   behind `P:` and maps the drive letter back by parsing `net use`. Lose that race and the
   entry path is mangled: "Failed to resolve entry for package @vitejs/plugin-react",
   roughly one run in two. `resolve.preserveSymlinks` below cannot help — it configures
   the app's module graph, which Vite only reads after this file has already loaded.

   So the plugin comes in through Node's own resolver, which handles the share correctly,
   and `defineConfig` is replaced by the JSDoc type it exists to provide. See vite.env.js
   for the rest of what a network drive breaks. */
const require = createRequire(import.meta.url)
const react = require('@vitejs/plugin-react').default

// `design/` holds the byte-exact Claude Design mirror. The React port imports the
// data layer and design-system CSS straight out of it so there is exactly one copy
// of each and re-importing from Claude Design cannot silently fork them.
/** @type {import('vite').UserConfig} */
export default {
  plugins: [react()],
  // See vite.env.js for what a network drive breaks and why these two settings fix it.
  resolve: { preserveSymlinks: onNetworkDrive },
  server: {
    port: 5173,
    fs: { allow: ['..'] },
    watch,
  },
}
