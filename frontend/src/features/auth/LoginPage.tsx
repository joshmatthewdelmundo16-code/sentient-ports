import { useState, type FormEvent } from 'react'
import { Navigate, useNavigate, useSearchParams } from 'react-router'
import { useQueryClient } from '@tanstack/react-query'
import { api } from '../../api/client'
import { useScope, type Session } from '../../app/scope'
import { Callout, Loading } from '../../shared/ui'

export function LoginPage() {
  const { session, loading } = useScope()
  const qc = useQueryClient()
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const next = params.get('next')
  const target = next && next.startsWith('/') && !next.startsWith('//') ? next : '/'

  if (loading) return <div className="page"><Loading /></div>
  if (session?.authenticated) {
    if (target.startsWith('/ui')) {
      window.location.assign(target)
      return null
    }
    return <Navigate to={target.replace(/^\/app/, '') || '/'} replace />
  }

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const fresh = await api<Session>('/api/auth/login', { json: { email, password }, scope: null })
      // Drop anything cached under the previous identity, then install the new session
      // directly (the login response IS the session) so the auth gate sees it at once.
      qc.removeQueries({ predicate: (q) => q.queryKey[0] !== 'session' })
      qc.setQueryData(['session'], fresh)
      if (target.startsWith('/ui')) window.location.assign(target)
      else navigate(target.replace(/^\/app/, '') || '/', { replace: true })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Sign-in failed.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div style={{ minHeight: '100vh', display: 'grid', placeItems: 'center', padding: 16, background: 'var(--nav)' }}>
      <div className="card" style={{ width: 'min(440px, 100%)', padding: 28 }}>
        <div className="row" style={{ marginBottom: 16 }}>
          <div className="brand-mark" aria-hidden="true">PN</div>
          <div>
            <h1 style={{ fontSize: '1.2rem' }}>Port decision platform</h1>
            <div className="small muted">Sign in to your organization's workspace</div>
          </div>
        </div>
        <form onSubmit={submit} className="stack">
          <div className="field">
            <label htmlFor="email">Email</label>
            <input id="email" className="input" type="email" autoComplete="username" required value={email}
              onChange={(e) => setEmail(e.target.value)} />
          </div>
          <div className="field">
            <label htmlFor="password">Password</label>
            <input id="password" className="input" type="password" autoComplete="current-password" required value={password}
              onChange={(e) => setPassword(e.target.value)} />
          </div>
          {error ? <Callout tone="danger">{error}</Callout> : null}
          <button className="btn btn-primary" type="submit" disabled={busy}>{busy ? 'Signing in…' : 'Sign in'}</button>
        </form>
        {session?.demo_accounts?.length ? (
          <div style={{ marginTop: 20 }}>
            <Callout tone="synthetic" title="Local demo accounts.">
              These synthetic accounts exist only in this local SQLite demo. They are never created in a shared database.
            </Callout>
            <div className="table-wrap" style={{ marginTop: 8 }}>
              <table className="table">
                <thead><tr><th>Account</th><th>Scope</th><th /></tr></thead>
                <tbody>
                  {session.demo_accounts.map((a) => (
                    <tr key={a.email}>
                      <td className="small"><strong>{a.email}</strong><div className="tiny muted">{a.role}</div></td>
                      <td className="small">{a.organization}</td>
                      <td>
                        <button type="button" className="btn btn-sm" data-testid={`use-${a.email}`}
                          onClick={() => { setEmail(a.email); setPassword((a as { password?: string }).password ?? '') }}>
                          Use
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ) : null}
      </div>
    </div>
  )
}
