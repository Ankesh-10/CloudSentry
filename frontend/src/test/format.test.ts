import { describe, expect, it } from 'vitest'

import { humanize, num, relTime, shortId, usd } from '../lib/format'

describe('format', () => {
  it('usd', () => {
    expect(usd(1234.5)).toBe('$1,234.50')
    expect(usd(null)).toBe('—')
  })

  it('num', () => {
    expect(num(3)).toBe('3')
    expect(num(0.12345)).toBe('0.12')
    expect(num(undefined)).toBe('—')
  })

  it('relTime', () => {
    const now = Date.parse('2026-10-04T12:00:00Z')
    expect(relTime('2026-10-04T11:59:30Z', now)).toBe('just now')
    expect(relTime('2026-10-04T11:45:00Z', now)).toBe('15m ago')
    expect(relTime('2026-10-04T09:00:00Z', now)).toBe('3h ago')
    expect(relTime('2026-10-01T12:00:00Z', now)).toBe('3d ago')
    expect(relTime('2026-10-04T14:00:00Z', now)).toBe('2h from now')
    expect(relTime(null, now)).toBe('—')
    expect(relTime('not a date', now)).toBe('—')
  })

  it('shortId / humanize', () => {
    expect(shortId('0123456789abcdef')).toBe('01234567')
    expect(humanize('pending_approval')).toBe('pending approval')
  })
})
