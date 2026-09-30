import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter, Route, Routes } from 'react-router'
import { ApiError } from './api/client'
import { AppShell } from './app/AppShell'
import { ROUTES } from './app/routes'
import { ScopeProvider } from './app/scope'
import { SelectionProvider } from './app/selection'
import { EmptyState } from './shared/ui'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 10_000,
      refetchOnWindowFocus: false,
      retry: (count, err) => !(err instanceof ApiError && err.status >= 400 && err.status < 500) && count < 2,
    },
  },
})

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
              <Route element={<AppShell />}>
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
