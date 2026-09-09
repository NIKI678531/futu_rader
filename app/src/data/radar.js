// The data layer is the mirrored `design/radar-data.js` verbatim — an IIFE that
// assigns `window.RADAR`. Importing it for its side effect keeps a single source of
// truth: re-import the design project and the app picks up the new data with no port.
import '../../../design/radar-data.js'

const R = window.RADAR

if (!R) {
  throw new Error('radar-data.js did not define window.RADAR')
}

export default R
