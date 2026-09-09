import { Link } from 'react-router-dom'
import { hrefToPath, isExternal } from '../lib/routes'

/* Drop-in for the `<a href="…dc.html">` links in the design files: routes internally,
   falls back to a plain anchor for the futunn.com deep links. */
export default function DcLink({ href, children, ...rest }) {
  if (!href || isExternal(href) || rest.target === '_blank') {
    return (
      <a href={href} {...rest}>
        {children}
      </a>
    )
  }
  return (
    <Link to={hrefToPath(href)} {...rest}>
      {children}
    </Link>
  )
}
