import type { Session } from '@supabase/supabase-js'
import { useQueryClient } from '@tanstack/react-query'
import { createContext, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'

import { isDemo } from '../lib/env'
import { resetRole } from '../lib/role'
import { authErrorMessage, supabase } from '../lib/supabase'

interface AuthState {
  session: Session | null
  loading: boolean
  email: string | null
  // Hint only: the API also grants roles via OPERATOR_USER_IDS / VIEWER_USER_IDS,
  // which the browser cannot see. The server's 403 is authoritative.
  roleHint: string | null
  // True after the user followed a password-recovery email link.
  recovering: boolean
  signIn: (email: string, password: string) => Promise<string | null>
  signOut: () => Promise<void>
  requestPasswordReset: (email: string) => Promise<string | null>
  updatePassword: (password: string) => Promise<string | null>
}

const AuthContext = createContext<AuthState | null>(null)

const DEMO_SESSION = {
  access_token: 'demo',
  user: { id: 'demo', email: 'demo@cloudsentry.local', app_metadata: { cloudsentry_role: 'operator' } },
} as unknown as Session

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(isDemo ? DEMO_SESSION : null)
  const [loading, setLoading] = useState(!isDemo && !!supabase)
  const [recovering, setRecovering] = useState(false)
  const userId = useRef<string | null>(null)
  const queryClient = useQueryClient()

  useEffect(() => {
    if (isDemo || !supabase) return
    // INITIAL_SESSION fires first with the stored session (or null), then
    // SIGNED_IN / TOKEN_REFRESHED / SIGNED_OUT / PASSWORD_RECOVERY as they
    // happen — including in other tabs. Never call Supabase from inside this
    // callback (it holds the auth lock); only update React state here.
    const { data } = supabase.auth.onAuthStateChange((event, next) => {
      const nextId = next?.user.id ?? null
      if (nextId !== userId.current) {
        // A different identity (or none): nothing cached may leak across.
        queryClient.clear()
        resetRole()
        userId.current = nextId
      }
      if (event === 'PASSWORD_RECOVERY') setRecovering(true)
      if (event === 'SIGNED_OUT') setRecovering(false)
      setSession(next)
      setLoading(false)
    })
    return () => data.subscription.unsubscribe()
  }, [queryClient])

  const value = useMemo<AuthState>(() => {
    const role = session?.user.app_metadata?.cloudsentry_role
    return {
      session,
      loading,
      recovering,
      email: session?.user.email ?? null,
      roleHint: typeof role === 'string' ? role : null,
      async signIn(email, password) {
        if (!supabase) return 'Supabase is not configured'
        const { error } = await supabase.auth.signInWithPassword({ email: email.trim(), password })
        return authErrorMessage(error)
      },
      async signOut() {
        if (isDemo || !supabase) return
        // Local scope: signs out this browser only, not the user's other devices.
        await supabase.auth.signOut({ scope: 'local' })
      },
      async requestPasswordReset(email) {
        if (!supabase) return 'Supabase is not configured'
        const { error } = await supabase.auth.resetPasswordForEmail(email.trim(), {
          redirectTo: `${window.location.origin}/reset-password`,
        })
        return authErrorMessage(error)
      },
      async updatePassword(password) {
        if (!supabase) return 'Supabase is not configured'
        const { error } = await supabase.auth.updateUser({ password })
        if (!error) setRecovering(false)
        return authErrorMessage(error)
      },
    }
  }, [session, loading, recovering])

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used inside <AuthProvider>')
  return ctx
}
