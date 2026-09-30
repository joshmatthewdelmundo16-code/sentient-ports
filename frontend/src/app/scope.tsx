/**
 * Who is signed in, and which organization (scope) they are acting in.
 *
 * The server is the authority: /api/session reports the user, their memberships and the
 * active organization; every request carries X-Scope-Org and the server refuses scopes the
 * user does not belong to. This context only remembers the user's choice and shows it.
 *
 * Against a backend without /api/session (pre-D26), it falls back to a single local
 * workspace so the product still renders.
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api, ApiError, setActiveScope, UNAUTHENTICATED_EVENT } from '../api/client'

export interface Organization {
  id: string
  key: string
  name: string
  kind: 'port' | 'regional_hub' | 'national_hub' | 'network' | 'sandbox' | string
  parent_id: string | null
  synthetic?: boolean
}

export interface Membership {
  organization: Organization
  role: 'viewer' | 'analyst' | 'approver' | 'admin' | string
}

export interface Session {
  authenticated: boolean
  auth_mode: 'required' | 'local' | string
  user: { id: string; email: string; display_name: string } | null
  memberships: Membership[]
  organizations: Organization[]
  active_organization_id: string | null
  demo_accounts?: { email: string; role: string; organization: string; password?: string }[] | null
}

interface ScopeValue {
  session: Session | null
  loading: boolean
  error: unknown
  scope: Organization | null
  role: string | null
  setScope: (orgId: string) => void
  can: (need: 'viewer' | 'analyst' | 'approver' | 'admin') => boolean
  refresh: () => void
}

const RANK: Record<string, number> = { viewer: 1, analyst: 2, approver: 3, admin: 4 }
const STORAGE_KEY = 'platform.scope'

const ScopeContext = createContext<ScopeValue | null>(null)

const LOCAL_FALLBACK: Session = {
  authenticated: true,
  auth_mode: 'local',
  user: null,
  memberships: [],
  organizations: [],
  active_organization_id: null,
}

export function kindLabel(kind: string | undefined): string {
  switch (kind) {
    case 'port': return 'Port private zone'
    case 'regional_hub': return 'Regional hub'
    case 'national_hub': return 'National hub'
    case 'network': return 'Port network'
    case 'sandbox': return 'Engine sandbox'
    default: return 'Workspace'
  }
}

export function ScopeProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient()
  const [chosen, setChosen] = useState<string | null>(() => {
    try { return window.localStorage.getItem(STORAGE_KEY) } catch { return null }
  })
  const query = useQuery({
    queryKey: ['session'],
    queryFn: async () => {
      try {
        return await api<Session>('/api/session', { scope: null })
      } catch (err) {
        if (err instanceof ApiError && err.status === 404) return LOCAL_FALLBACK
        throw err
      }
    },
    retry: false,
    staleTime: 30_000,
  })
  const session = query.data ?? null

  const memberships = useMemo(() => session?.memberships ?? [], [session])
  const scopeId = useMemo(() => {
    if (!session) return null
    const ids = new Set(memberships.map((m) => m.organization.id))
    if (chosen && ids.has(chosen)) return chosen
    if (session.active_organization_id && ids.has(session.active_organization_id)) return session.active_organization_id
    return memberships[0]?.organization.id ?? null
  }, [session, memberships, chosen])

  // Set synchronously during render so the very first child queries carry the header.
  setActiveScope(scopeId)

  useEffect(() => {
    const onUnauth = () => void qc.invalidateQueries({ queryKey: ['session'] })
    window.addEventListener(UNAUTHENTICATED_EVENT, onUnauth)
    return () => window.removeEventListener(UNAUTHENTICATED_EVENT, onUnauth)
  }, [qc])

  const setScope = useCallback((orgId: string) => {
    try { window.localStorage.setItem(STORAGE_KEY, orgId) } catch { /* private mode */ }
    setActiveScope(orgId)
    // No invalidation: every scoped query key carries the scope, so switching simply moves
    // to a different set of cache entries. Refetching the old ones would send their
    // identifiers under the new scope.
    setChosen(orgId)
  }, [])

  const membership = memberships.find((m) => m.organization.id === scopeId) ?? null
  const role = membership?.role ?? (session?.auth_mode === 'local' ? 'admin' : null)
  const value: ScopeValue = {
    session,
    loading: query.isLoading,
    error: query.error,
    scope: membership?.organization ?? null,
    role,
    setScope,
    can: (need) => (RANK[role ?? ''] ?? 0) >= RANK[need]!,
    refresh: () => void query.refetch(),
  }
  return <ScopeContext.Provider value={value}>{children}</ScopeContext.Provider>
}

export function useScope(): ScopeValue {
  const v = useContext(ScopeContext)
  if (!v) throw new Error('useScope must be used inside ScopeProvider')
  return v
}
