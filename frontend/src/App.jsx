import { Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { cloneElement, startTransition, useEffect, useState } from 'react'
import { startLiveUpdates } from './lib/api'
import ScreenBoundary from './components/ScreenBoundary'
import OfficialActivity from './screens/OfficialActivity'
import KolActivity from './screens/KolActivity'
import KolDetail from './screens/KolDetail'
import ProductMonitor from './screens/productMonitor/index.jsx'
import SectorOverview from './screens/sectorOverview/index.jsx'

/* The ported screens read `location.search` directly, exactly as the .dc.html
   sources do. Keying each route on the full URL remounts on navigation so those
   reads stay correct instead of going stale across an in-place route change.

   Each screen also sits inside a ScreenBoundary: the data façade now reads from
   the backend, and api.js keeps the screens synchronous by throwing a Promise on
   a cache miss (Suspense) or an Error when the service is unreachable. Both need
   a boundary above the screen to land in. Keying the boundary too means a route
   change also clears a stuck error state. */
export default function App() {
  const loc = useLocation()
  const [dataVersion, setDataVersion] = useState(0)
  useEffect(() => startLiveUpdates(() => startTransition(() => setDataVersion(version => version + 1))), [])
  const key = loc.pathname + loc.search

  const screen = (element) => <ScreenBoundary key={key}>{cloneElement(element, { dataVersion })}</ScreenBoundary>

  return (
    <Routes>
      <Route path="/" element={<Navigate to="/official" replace />} />
      <Route path="/official" element={screen(<OfficialActivity />)} />
      <Route path="/kol" element={screen(<KolActivity />)} />
      <Route path="/kol/detail" element={screen(<KolDetail />)} />
      <Route path="/product" element={screen(<ProductMonitor />)} />
      <Route path="/sector" element={screen(<SectorOverview />)} />
      <Route path="*" element={<Navigate to="/official" replace />} />
    </Routes>
  )
}
