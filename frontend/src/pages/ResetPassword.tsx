import { useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'

import { useAuth } from '../auth/AuthProvider'
import { toast } from '../components/Toast'
import { Button, ErrorBox, Loading } from '../components/ui'
import { AuthShell, Field } from './Login'

const MIN_LENGTH = 8

// Landing page for the password-recovery email. Supabase (PKCE) exchanges the
// code in the link for a short-lived session; we then ask for a new password.
export default function ResetPassword() {
  const { session, loading, updatePassword } = useAuth()
  const navigate = useNavigate()
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  if (loading) return <AuthShell title="Reset password"><Loading label="Checking link" /></AuthShell>

  if (!session) {
    return (
      <AuthShell title="Link expired" subtitle="This reset link is invalid or has already been used. Links are single-use and expire after an hour.">
        <Link to="/login?mode=forgot" className="text-[13.5px] font-medium text-info hover:underline">Send a new link</Link>
      </AuthShell>
    )
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    if (password.length < MIN_LENGTH) return setError(`Use at least ${MIN_LENGTH} characters.`)
    if (password !== confirm) return setError('The two passwords do not match.')
    setBusy(true)
    const err = await updatePassword(password)
    setBusy(false)
    if (err) return setError(err)
    toast.success('Password updated', 'You are signed in.')
    navigate('/', { replace: true })
  }

  return (
    <AuthShell title="New password" subtitle={`For ${session.user.email}`}>
      <form onSubmit={onSubmit} className="space-y-5">
        <Field label="New password">
          <input className="input h-10 w-full" type="password" autoComplete="new-password" required autoFocus
                 minLength={MIN_LENGTH} value={password} onChange={(e) => setPassword(e.target.value)} />
        </Field>
        <Field label="Confirm password">
          <input className="input h-10 w-full" type="password" autoComplete="new-password" required
                 value={confirm} onChange={(e) => setConfirm(e.target.value)} />
        </Field>
        {error && <ErrorBox error={error} title="Password" />}
        <Button type="submit" variant="primary" className="h-10 w-full" disabled={busy}>
          {busy ? 'Saving…' : 'Update password'}
        </Button>
      </form>
    </AuthShell>
  )
}
