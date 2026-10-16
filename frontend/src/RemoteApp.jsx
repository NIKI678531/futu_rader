import '../../design/_ds/csop-design-system-019e159a-9eb8-740d-9d7d-8a5f9a550a1c/colors_and_type.css'
import './styles/tokens.css'
import App from './App'

export const HOST_ROUTE = '/opinion-radar'

export default function RemoteApp({ basePath = HOST_ROUTE, allowAdmin = true }) {
  return <div className="opinion-radar-app"><App basePath={basePath} includeAdmin={allowAdmin} /></div>
}
