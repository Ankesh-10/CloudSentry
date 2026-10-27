import { createClient, type AuthError, type SupabaseClient } from '@supabase/supabase-js'

import { env, missingConfig } from './env'

// Supabase is used for sign-in only; all data goes through the CloudSentry API,
// which enforces roles, rate limits and the audit trail.
export const supabase: SupabaseClient | null = missingConfig().length || !env.supabaseUrl
  ? null
  : createClient(env.supabaseUrl, env.supabaseAnonKey, {
      auth: {
        persistSession: true,
        autoRefreshToken: true,
        // PKCE: email links (password recovery) carry a one-time code that is
        // exchanged by this browser, not a token in the URL fragment.
        flowType: 'pkce',
        detectSessionInUrl: true,
        storageKey: 'cloudsentry-auth',
      },
    })

export async function getAccessToken(): Promise<string | null> {
  if (!supabase) return null
  const { data } = await supabase.auth.getSession()
  return data.session?.access_token ?? null
}

// Called when the API rejects our token: a revoked or clock-skewed token can
// often be fixed by one refresh. Returns null when the session is gone.
export async function refreshAccessToken(): Promise<string | null> {
  if (!supabase) return null
  const { data, error } = await supabase.auth.refreshSession()
  return error ? null : (data.session?.access_token ?? null)
}

// Set when the session had to be dropped; the login page reads it once to
// explain why the user is back there.
let sessionExpired = false

export async function expireSession(): Promise<void> {
  sessionExpired = true
  await supabase?.auth.signOut({ scope: 'local' })
}

export function consumeSessionExpired(): boolean {
  const v = sessionExpired
  sessionExpired = false
  return v
}

// Supabase Auth error codes -> messages a person can act on.
const AUTH_MESSAGES: Record<string, string> = {
  invalid_credentials: 'Email or password is incorrect.',
  email_not_confirmed: 'Confirm your email address first — check your inbox for the link.',
  user_banned: 'This account has been disabled. Contact an administrator.',
  over_request_rate_limit: 'Too many attempts. Wait a minute and try again.',
  over_email_send_rate_limit: 'Too many emails sent. Wait a few minutes before requesting another.',
  weak_password: 'Choose a stronger password (at least 8 characters, mixing letters and numbers).',
  same_password: 'The new password must be different from the current one.',
  session_not_found: 'Your session has ended. Sign in again.',
  session_expired: 'Your session has expired. Sign in again.',
}

export function authErrorMessage(err: AuthError | null | undefined): string | null {
  if (!err) return null
  if (err.code && AUTH_MESSAGES[err.code]) return AUTH_MESSAGES[err.code]
  if (err.name === 'AuthRetryableFetchError' || err.status === 0) return 'Cannot reach Supabase. Check your connection and try again.'
  return err.message || 'Authentication failed.'
}
