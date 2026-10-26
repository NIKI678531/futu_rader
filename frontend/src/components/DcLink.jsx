import { Link } from 'react-router-dom'
import R from '../data/radar'
import { hrefToPath, isExternal } from '../lib/routes'
import { useAppBasePath, withAppBasePath } from '../lib/appRoutes'
import { inheritCurrentRange } from '../lib/customRange'

/* Drop-in for the `<a href="…dc.html">` links in the design files: routes internally,
   falls back to a plain anchor for the futunn.com deep links. */
export default function DcLink({ href, children, ...rest }) {
  const basePath = useAppBasePath()
  if (!href || isExternal(href) || rest.target === '_blank') {
    return (
      <a href={href} {...rest}>
        {children}
      </a>
    )
  }
  href = inheritCurrentRange(href, R.PRESETS)
  return (
    <Link to={withAppBasePath(basePath, hrefToPath(href))} {...rest}>
      {children}
    </Link>
  )
}
