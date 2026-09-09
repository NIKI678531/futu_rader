import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// `design/` holds the byte-exact Claude Design mirror. The React port imports the
// data layer and design-system CSS straight out of it so there is exactly one copy
// of each and re-importing from Claude Design cannot silently fork them.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    fs: { allow: ['..'] },
  },
})
