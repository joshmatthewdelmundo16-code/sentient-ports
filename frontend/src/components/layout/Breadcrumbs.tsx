import { Link } from 'react-router'
import { NAV } from '../../app/nav'
import { Icon } from '../ui/Icon'
import { resolveCrumb } from './shell'

export function Breadcrumbs({ pathname }: { pathname: string }) {
  const crumb = resolveCrumb(pathname, NAV)
  const sep = (
    <span className="sep" aria-hidden="true">
      <Icon name="chevr" size={12} />
    </span>
  )
  return (
    <nav className="crumbs" aria-label="Breadcrumb">
      <Link to="/">Sentient Ports</Link>
      {crumb?.group ? <>{sep}<span>{crumb.group}</span></> : null}
      {sep}
      <span className="cur" aria-current="page">{crumb?.label ?? 'Page Not Found'}</span>
    </nav>
  )
}
