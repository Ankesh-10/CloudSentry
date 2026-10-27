import { describe, expect, it } from 'vitest'

import { describePending, stageOf } from '../components/PendingChanges'
import type { PendingChange } from '../lib/types'

const T = Date.parse('2026-10-25T12:00:00Z')
const MIN = 60_000

function req(over: Partial<PendingChange> = {}): PendingChange {
  return {
    key: 'DRY_RUN_MODE', value: 'false', requested_by: 'USER:me',
    requested_at: new Date(T).toISOString(), expires_at: new Date(T + 60 * MIN).toISOString(),
    expired: false, confirmable_at: new Date(T + 15 * MIN).toISOString(), ...over,
  }
}

describe('stageOf', () => {
  it('makes the requester wait until confirmable_at in solo mode', () => {
    expect(stageOf(req(), true, T + 5 * MIN)).toEqual({ kind: 'waiting', until: T + 15 * MIN })
    expect(stageOf(req(), true, T + 15 * MIN)).toEqual({ kind: 'ready' })
  })
  it('lets another operator confirm at once', () => {
    expect(stageOf(req(), false, T + MIN)).toEqual({ kind: 'ready' })
  })
  it('leaves the requester waiting for someone else in two-person mode', () => {
    expect(stageOf(req({ confirmable_at: null }), true, T + 30 * MIN)).toEqual({ kind: 'others' })
  })
  it('treats a request past its expiry as expired even before the server says so', () => {
    expect(stageOf(req(), true, T + 61 * MIN)).toEqual({ kind: 'expired' })
    expect(stageOf(req({ expired: true }), false, T)).toEqual({ kind: 'expired' })
  })
})

describe('describePending', () => {
  it('names changes in plain words', () => {
    expect(describePending(req())).toBe('Turn dry-run off')
    expect(describePending(req({ key: 'GLOBAL_AUTOMATION_ENABLED', value: 'true' }))).toBe('Turn automation on')
    expect(describePending(req({ key: 'POLICY:abc:requires_approval', value: false }))).toBe('Let a policy run without approval')
    expect(describePending(req({ key: 'RESOURCE:abc:protected', value: false }))).toBe('Unprotect a resource')
    expect(describePending(req({ key: 'MAX_ACTIONS_PER_DAY', value: '20' }))).toBe('Set MAX_ACTIONS_PER_DAY to 20')
  })
})
