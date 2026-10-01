import { useQueryClient } from '@tanstack/react-query'
import { api } from '../../api/client'
import { kindLabel, useScope } from '../../app/scope'
import { Badge } from '../../shared/ui'
import { Menu } from '../ui/Menu'
import { initialsOf } from './shell'

export function UserMenu() {
  const { session, scope, role } = useScope()
  const qc = useQueryClient()
  const user = session?.user
  if (!user) {
    return session?.auth_mode === 'local'
      ? <Badge tone="warning" title="AUTH_MODE=local — single-developer mode, SQLite only">Local Mode</Badge>
      : null
  }

  const signOut = async () => {
    await api('/api/auth/logout', { method: 'POST' }).catch(() => undefined)
    qc.clear()
    window.location.assign('/app/login')
  }

  return (
    <Menu
      align="end"
      label="Account"
      items={[{ value: 'signout', label: 'Sign Out' }]}
      onSelect={() => void signOut()}
      header={
        <div className="pm-head">
          <div className="pm-name">{user.display_name}</div>
          <div className="pm-sub">{user.email}</div>
          {role || scope ? <div className="pm-sub">{[role, scope ? `${scope.name} · ${kindLabel(scope.kind)}` : null].filter(Boolean).join(' · ')}</div> : null}
        </div>
      }
      trigger={(p) => (
        <button type="button" className="avatar" aria-label={`Account: ${user.display_name}`} title={`${user.display_name}${role ? ` · ${role}` : ''}`} {...p}>
          {initialsOf(user.display_name)}
        </button>
      )}
    />
  )
}
