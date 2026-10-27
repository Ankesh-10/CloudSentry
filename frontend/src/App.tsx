import { lazy, Suspense, type ReactNode } from 'react'
import { Navigate, Route, Routes, useLocation } from 'react-router-dom'

import { useAuth } from './auth/AuthProvider'
import Layout from './components/Layout'
import { Loading } from './components/ui'
import { missingConfig } from './lib/env'
import Login from './pages/Login'

// Pages load on demand so the charting library is not in the initial bundle.
const Actions = lazy(() => import('./pages/Actions'))
const Anomalies = lazy(() => import('./pages/Anomalies'))
const AuditLogs = lazy(() => import('./pages/AuditLogs'))
const Dashboard = lazy(() => import('./pages/Dashboard'))
const ResetPassword = lazy(() => import('./pages/ResetPassword'))
const ResourceDetail = lazy(() => import('./pages/ResourceDetail'))
const Resources = lazy(() => import('./pages/Resources'))
const System = lazy(() => import('./pages/System'))

function RequireAuth({ children }: { children: ReactNode }) {
  const { session, loading } = useAuth()
  const location = useLocation()
  if (loading) return <Loading label="Checking session" />
  if (!session) return <Navigate to="/login" replace state={{ from: location.pathname }} />
  return <>{children}</>
}

function SetupRequired({ missing }: { missing: string[] }) {
  return (
    <div className="mx-auto mt-24 max-w-lg rounded-xl border border-line bg-surface p-8">
      <div className="eyebrow mb-3 text-accent">Configuration</div>
      <h1 className="display text-[2.2rem] text-fg">Not configured</h1>
      <p className="mt-4 text-[13.5px] text-fg-2">
        Copy <code className="font-mono">frontend/.env.example</code> to{' '}
        <code className="font-mono">frontend/.env.local</code>, set the values below, and restart{' '}
        <code className="font-mono">npm run dev</code>.
      </p>
      <ul className="mt-3 list-inside list-disc font-mono text-sm text-fg-2">
        {missing.map((m) => (
          <li key={m}>{m}</li>
        ))}
      </ul>
    </div>
  )
}

export default function App() {
  const missing = missingConfig()
  if (missing.length) return <SetupRequired missing={missing} />

  return (
    <Suspense fallback={<Loading />}>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/reset-password" element={<ResetPassword />} />
        <Route
          element={
            <RequireAuth>
              <Layout />
            </RequireAuth>
          }
        >
          <Route index element={<Dashboard />} />
          <Route path="resources" element={<Resources />} />
          <Route path="resources/:id" element={<ResourceDetail />} />
          <Route path="anomalies" element={<Anomalies />} />
          <Route path="actions" element={<Actions />} />
          <Route path="audit" element={<AuditLogs />} />
          <Route path="system" element={<System />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Suspense>
  )
}
