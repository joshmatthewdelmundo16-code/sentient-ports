import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { BrowserRouter, Navigate, Route, Routes, useLocation } from 'react-router'
import { ApiError } from './api/client'
import { AppShell } from './app/AppShell'
import { ROUTES } from './app/routes'
import { ScopeProvider, useScope } from './app/scope'
import { LoginPage } from './features/auth/LoginPage'
import { SelectionProvider } from './app/selection'
import { EmptyState, ErrorState, Loading } from './shared/ui'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 10_000,
      refetchOnWindowFocus: false,
      retry: (count, err) => !(err instanceof ApiError && err.status >= 400 && err.status < 500) && count < 2,
    },
  },
})

/** Sends a signed-out user to the sign-in page. Access itself is enforced by the server. */
function RequireAuth({ children }: { children: ReactNode }) {
  const { session, loading, error } = useScope()
  const location = useLocation()
  if (loading) return <div className="page"><Loading lines={4} /></div>
  if (error) return <div className="page"><ErrorState error={error} /></div>
  if (session && session.auth_mode === 'required' && !session.authenticated) {
    return <Navigate to={`/login?next=${encodeURIComponent(location.pathname)}`} replace />
  }
  return <>{children}</>
}

function NotFound() {
  return <EmptyState title="Page not found">This page does not exist. Use the navigation to continue.</EmptyState>
}

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter basename="/app">
        <ScopeProvider>
          <SelectionProvider>
            <Routes>
              <Route path="/login" element={<LoginPage />} />
              <Route element={<RequireAuth><AppShell /></RequireAuth>}>
                {ROUTES.map((r) => <Route key={r.path} path={r.path} element={r.element} />)}
                <Route path="*" element={<NotFound />} />
              </Route>
            </Routes>
          </SelectionProvider>
        </ScopeProvider>
      </BrowserRouter>
    </QueryClientProvider>
  )
}
