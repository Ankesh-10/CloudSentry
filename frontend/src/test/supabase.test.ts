import type { AuthError } from '@supabase/supabase-js'
import { describe, expect, it, vi } from 'vitest'

vi.mock('../lib/env', () => ({ env: { supabaseUrl: '', supabaseAnonKey: '' }, missingConfig: () => ['x'] }))

import { authErrorMessage, consumeSessionExpired, expireSession } from '../lib/supabase'

const err = (fields: Partial<AuthError>) => ({ name: 'AuthApiError', message: 'raw', status: 400, ...fields }) as AuthError

describe('authErrorMessage', () => {
  it('maps known Supabase codes to actionable text', () => {
    expect(authErrorMessage(err({ code: 'invalid_credentials' }))).toBe('Email or password is incorrect.')
    expect(authErrorMessage(err({ code: 'email_not_confirmed' }))).toMatch(/Confirm your email/)
    expect(authErrorMessage(err({ code: 'over_request_rate_limit', status: 429 }))).toMatch(/Too many attempts/)
  })

  it('explains network failures', () => {
    expect(authErrorMessage(err({ name: 'AuthRetryableFetchError', status: 0 }))).toMatch(/Cannot reach Supabase/)
  })

  it('falls back to the raw message, and null for no error', () => {
    expect(authErrorMessage(err({ code: 'something_new', message: 'Something new happened' }))).toBe('Something new happened')
    expect(authErrorMessage(null)).toBeNull()
  })
})

describe('session expiry flag', () => {
  it('is set by expireSession and read exactly once', async () => {
    expect(consumeSessionExpired()).toBe(false)
    await expireSession()
    expect(consumeSessionExpired()).toBe(true)
    expect(consumeSessionExpired()).toBe(false)
  })
})
