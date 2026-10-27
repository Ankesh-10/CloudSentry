import { useState, type FormEvent, type ReactNode } from 'react'
import { Navigate, useLocation, useSearchParams } from 'react-router-dom'

import { ShieldCheckIcon } from '@phosphor-icons/react'

import { useAuth } from '../auth/AuthProvider'
import { Button, ErrorBox, Notice, useDocumentTitle } from '../components/ui'
import { consumeSessionExpired } from '../lib/supabase'

export function AuthShell({ title, subtitle, children }: { title: string; subtitle?: ReactNode; children: ReactNode }) {
  useDocumentTitle(title)
  return (
    <div className="grid min-h-screen lg:grid-cols-[1.1fr_1fr]">
      <aside className="relative hidden flex-col justify-between border-r border-line px-12 py-12 lg:flex">
        <div className="flex items-center gap-2.5">
          <span className="grid h-7 w-7 place-items-center rounded-md bg-primary text-on-primary">
            <ShieldCheckIcon size={16} weight="fill" />
          </span>
          <span className="text-[15px] font-semibold tracking-[-0.01em] text-fg">CloudSentry</span>
        </div>
        <div className="max-w-lg">
          <h2 className="display text-[clamp(2.6rem,4.4vw,3.75rem)] text-fg">Find idle spend before the invoice does.</h2>
          <p className="mt-5 text-[15px] text-muted">
            CloudSentry watches your cloud accounts, flags cost anomalies, and proposes reversible fixes that a human approves.
          </p>
          <dl className="mt-10 grid grid-cols-2 gap-px overflow-hidden rounded-xl border border-line bg-line">
            {[
              ['Discovery', 'Every 15 minutes'],
              ['Telemetry', 'CloudWatch, every 5 minutes'],
              ['Detection', 'Rules, z-score, Isolation Forest'],
              ['Actions', 'Reversible, audited, kill-switch'],
            ].map(([k, v]) => (
              <div key={k} className="bg-surface px-5 py-4">
                <dt className="text-[12.5px] text-muted">{k}</dt>
                <dd className="mt-1 text-[13.5px] font-medium text-fg">{v}</dd>
              </div>
            ))}
          </dl>
        </div>
        <p className="text-[12.5px] text-muted">Autonomous cloud cost intelligence</p>
      </aside>
      <main className="flex flex-col justify-center px-6 py-12 sm:px-12">
        <div className="mx-auto w-full max-w-[380px]">
          <div className="mb-10 flex items-center gap-2.5 lg:hidden">
            <span className="grid h-7 w-7 place-items-center rounded-md bg-primary text-on-primary">
              <ShieldCheckIcon size={16} weight="fill" />
            </span>
            <span className="text-[15px] font-semibold text-fg">CloudSentry</span>
          </div>
          <h1 className="display text-[2.4rem] text-fg">{title}</h1>
          {subtitle && <p className="mt-2 text-[14px] text-muted">{subtitle}</p>}
          <div className="mt-8">{children}</div>
        </div>
      </main>
    </div>
  )
}

export function Field({ label, children, aside }: { label: string; children: ReactNode; aside?: ReactNode }) {
  return (
    <label className="block space-y-1.5">
      <span className="flex items-center justify-between text-[13px] font-medium text-fg">
        {label}
        {aside}
      </span>
      {children}
    </label>
  )
}

export default function Login() {
  const { session, signIn, requestPasswordReset } = useAuth()
  const location = useLocation()
  const [params, setParams] = useSearchParams()
  const mode = params.get('mode') === 'forgot' ? 'forgot' : 'signin'
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [sentTo, setSentTo] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  // Read once on mount: set when the API rejected the session and it was dropped.
  const [expired] = useState(consumeSessionExpired)

  const from = (location.state as { from?: string } | null)?.from ?? '/'
  if (session) return <Navigate to={from} replace />

  function switchMode(next: 'signin' | 'forgot') {
    setError(null)
    setSentTo(null)
    setParams(next === 'forgot' ? { mode: 'forgot' } : {}, { replace: true })
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    if (mode === 'forgot') {
      const err = await requestPasswordReset(email)
      if (err) setError(err)
      else setSentTo(email.trim())
    } else {
      setError(await signIn(email, password))
    }
    setBusy(false)
  }

  if (mode === 'forgot') {
    return (
      <AuthShell title="Reset password" subtitle="We'll email you a link to choose a new one.">
        {sentTo ? (
          <div className="space-y-4">
            <Notice tone="good">
              If an account exists for <span className="font-semibold text-fg">{sentTo}</span>, a reset link is on its way. It expires in an hour.
            </Notice>
            <button type="button" onClick={() => switchMode('signin')} className="text-[13px] font-medium text-fg hover:underline">
              Back to sign in
            </button>
          </div>
        ) : (
          <form onSubmit={onSubmit} className="space-y-5">
            <Field label="Email">
              <input className="input h-10 w-full" type="email" autoComplete="username" required autoFocus value={email}
                     onChange={(e) => setEmail(e.target.value)} />
            </Field>
            {error && <ErrorBox error={error} title="Reset" />}
            <Button type="submit" variant="primary" className="h-10 w-full" disabled={busy}>
              {busy ? 'Sending…' : 'Send reset link'}
            </Button>
            <button type="button" onClick={() => switchMode('signin')} className="w-full text-center text-[13px] text-muted hover:text-fg">
              Back to sign in
            </button>
          </form>
        )}
      </AuthShell>
    )
  }

  return (
    <AuthShell title="Sign in" subtitle="Use your Supabase account. An administrator grants viewer or operator access.">
      <form onSubmit={onSubmit} className="space-y-5">
        {expired && <Notice tone="warning">Your session expired. Sign in again to continue.</Notice>}
        <Field label="Email">
          <input className="input h-10 w-full" type="email" autoComplete="username" required autoFocus value={email}
                 onChange={(e) => setEmail(e.target.value)} />
        </Field>
        <Field
          label="Password"
          aside={
            <button type="button" onClick={() => switchMode('forgot')} className="text-[12.5px] font-normal text-muted hover:text-fg">
              Forgot password?
            </button>
          }
        >
          <input className="input h-10 w-full" type="password" autoComplete="current-password" required value={password}
                 onChange={(e) => setPassword(e.target.value)} />
        </Field>
        {error && <ErrorBox error={error} title="Sign in" />}
        <Button type="submit" variant="primary" className="h-10 w-full" disabled={busy}>
          {busy ? 'Signing in…' : 'Continue'}
        </Button>
      </form>
    </AuthShell>
  )
}
