import { Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { cloneElement, startTransition, useEffect, useState } from 'react'
import { startLiveUpdates } from './lib/api'
import ScreenBoundary from './components/ScreenBoundary'
import { LoadingBar } from './components/LoadingSkeleton'
import ProgressDrawer from './components/ProgressDrawer'
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
   change also clears a stuck error state.

   `nav` 告诉边界这条路由属于哪个域、哪一页：Suspense fallback 里的骨架屏要在数据到达
   之前就把导航画出来（LoadingSkeleton），而导航高亮是纯函数算的，不用等 /meta。

   根上还挂两样跨路由存活的东西：顶部 2px 进度条（LoadingBar，读 api.js 的在途统计）
   与处理进度侧栏（ProgressDrawer，默认不渲染任何内容；入口按钮在 Shell 里，只在
   `DATA_PROVIDER=sql` 时出现）。挂在根上而不是屏幕里，是为了切路由时进度条不闪、
   侧栏不被重挂关掉。 */
export default function App() {
  const loc = useLocation()
  const [dataVersion, setDataVersion] = useState(0)
  useEffect(() => startLiveUpdates(() => startTransition(() => setDataVersion(version => version + 1))), [])
  const key = loc.pathname + loc.search

  const screen = (element, domain, sub) => (
    <ScreenBoundary key={key} nav={{ domain, sub }}>{cloneElement(element, { dataVersion })}</ScreenBoundary>
  )

  return (
    <>
      <LoadingBar />
      <Routes>
        <Route path="/" element={<Navigate to="/official" replace />} />
        <Route path="/official" element={screen(<OfficialActivity />, 'accounts', 'official')} />
        <Route path="/kol" element={screen(<KolActivity />, 'accounts', 'kol')} />
        <Route path="/kol/detail" element={screen(<KolDetail />, 'accounts', 'kol')} />
        <Route path="/product" element={screen(<ProductMonitor />, 'portfolio', 'product')} />
        <Route path="/sector" element={screen(<SectorOverview />, 'portfolio', 'sector')} />
        <Route path="*" element={<Navigate to="/official" replace />} />
      </Routes>
      <ProgressDrawer />
    </>
  )
}
