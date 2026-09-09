import { Navigate, Route, Routes, useLocation } from 'react-router-dom'
import OfficialActivity from './screens/OfficialActivity'
import KolActivity from './screens/KolActivity'
import KolDetail from './screens/KolDetail'
import ProductMonitor from './screens/productMonitor/index.jsx'
import SectorOverview from './screens/sectorOverview/index.jsx'

/* The ported screens read `location.search` directly, exactly as the .dc.html
   sources do. Keying each route on the full URL remounts on navigation so those
   reads stay correct instead of going stale across an in-place route change. */
export default function App() {
  const loc = useLocation()
  const key = loc.pathname + loc.search

  return (
    <Routes>
      <Route path="/" element={<Navigate to="/official" replace />} />
      <Route path="/official" element={<OfficialActivity key={key} />} />
      <Route path="/kol" element={<KolActivity key={key} />} />
      <Route path="/kol/detail" element={<KolDetail key={key} />} />
      <Route path="/product" element={<ProductMonitor key={key} />} />
      <Route path="/sector" element={<SectorOverview key={key} />} />
      <Route path="*" element={<Navigate to="/official" replace />} />
    </Routes>
  )
}
