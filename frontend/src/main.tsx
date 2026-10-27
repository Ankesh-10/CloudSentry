import { MutationCache, QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'

import App from './App'
import { AuthProvider } from './auth/AuthProvider'
import { Toaster } from './components/Toast'
import { TooltipProvider } from './components/ui'
import { ApiError } from './lib/api'
import { markReadOnly } from './lib/role'
import { ThemeProvider } from './theme/ThemeProvider'
import './index.css'

const queryClient = new QueryClient({
  // A 403 on a state-changing call means this account is a viewer: switch the
  // UI to read-only rather than letting every operator button fail again.
  mutationCache: new MutationCache({
    onError: (err) => {
      if (err instanceof ApiError && err.status === 403) markReadOnly()
    },
  }),
  defaultOptions: {
    queries: {
      staleTime: 15_000,
      refetchOnWindowFocus: true,
      // Auth/role/validation errors will not fix themselves on retry.
      retry: (count, err) => !(err instanceof ApiError && err.status >= 400 && err.status < 500) && count < 2,
    },
  },
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ThemeProvider>
      <QueryClientProvider client={queryClient}>
        <TooltipProvider>
          <BrowserRouter>
            <AuthProvider>
              <App />
            </AuthProvider>
          </BrowserRouter>
          <Toaster />
        </TooltipProvider>
      </QueryClientProvider>
    </ThemeProvider>
  </StrictMode>,
)
