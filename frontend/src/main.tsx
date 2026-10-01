import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { App } from './App'
import { ThemeProvider } from './components/layout/ThemeProvider'
import { ToastProvider } from './components/ui/Toast'
import { initTheme } from './components/layout/theme'
// Self-hosted (bundled) fonts: the app's CSP only allows font-src 'self'.
import '@fontsource-variable/inter/wght.css'
import '@fontsource-variable/jetbrains-mono/wght.css'
import './styles/tokens.css'
import './styles/legacy-aliases.css'
import './styles/base.css'
import './styles/layout.css'
import './styles/shell.css'
import './styles/primitives.css'
import './styles/data.css'
import './styles/ripple.css'
import './styles/network.css'
import './styles/overlays.css'
import './styles/components.css'

// Apply the saved theme before the first render so there is no flash of the wrong one.
initTheme()

createRoot(document.getElementById('root') as HTMLElement).render(
  <StrictMode>
    <ThemeProvider>
      <ToastProvider>
        <App />
      </ToastProvider>
    </ThemeProvider>
  </StrictMode>,
)
