import { afterEach, describe, expect, it, vi } from 'vitest'

const auth = vi.hoisted(() => ({
  getAccessToken: vi.fn(async () => 'tok-123' as string | null),
  refreshAccessToken: vi.fn(async () => 'tok-new' as string | null),
  expireSession: vi.fn(async () => {}),
}))
vi.mock('../lib/supabase', () => auth)

import { api, ApiError, buildUrl, errorMessage } from '../lib/api'

function respond(status: number, body: unknown, headers: Record<string, string> = {}) {
  return vi.fn(async () =>
    new Response(body === undefined ? '' : JSON.stringify(body), {
      status,
      headers: { 'Content-Type': 'application/json', ...headers },
    }),
  )
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.clearAllMocks()
})

async function rejection(p: Promise<unknown>): Promise<ApiError> {
  try {
    await p
  } catch (e) {
    return e as ApiError
  }
  throw new Error('expected the request to fail')
}

describe('buildUrl', () => {
  it('prefixes /api/v1 and drops empty params', () => {
    expect(buildUrl('/actions/', { status: undefined, limit: 5, actor: '' }, '')).toBe('/api/v1/actions/?limit=5')
  })

  it('uses an absolute base when configured', () => {
    expect(buildUrl('/health', undefined, 'https://api.example.com')).toBe('https://api.example.com/api/v1/health')
  })

  it('encodes query values', () => {
    expect(buildUrl('/audit-logs/', { since: '2026-01-01T00:00:00+00:00' }, '')).toBe(
      '/api/v1/audit-logs/?since=2026-01-01T00%3A00%3A00%2B00%3A00',
    )
  })
})

describe('errorMessage', () => {
  it('passes string detail through', () => {
    expect(errorMessage(409, { detail: 'Action is not awaiting approval' })).toBe('Action is not awaiting approval')
  })

  it('flattens FastAPI validation errors', () => {
    const body = { detail: [{ loc: ['query', 'limit'], msg: 'Input should be less than or equal to 500' }] }
    expect(errorMessage(422, body)).toBe('limit: Input should be less than or equal to 500')
  })

  it('falls back by status', () => {
    expect(errorMessage(403, null)).toMatch(/role/)
    expect(errorMessage(500, 'oops')).toBe('Request failed (HTTP 500)')
  })
})

describe('api', () => {
  it('sends the bearer token and JSON body', async () => {
    const fetchMock = respond(200, { status: 'success' })
    vi.stubGlobal('fetch', fetchMock)
    await api('/system/config', { method: 'PATCH', body: { key: 'DRY_RUN_MODE', value: 'true' } })
    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    const headers = init.headers as Record<string, string>
    expect(headers.Authorization).toBe('Bearer tok-123')
    expect(headers['Content-Type']).toBe('application/json')
    expect(init.body).toBe('{"key":"DRY_RUN_MODE","value":"true"}')
  })

  it('returns 202 bodies (two-person pending) as data', async () => {
    vi.stubGlobal('fetch', respond(202, { status: 'pending_confirmation', key: 'DRY_RUN_MODE' }))
    await expect(api('/system/config', { method: 'PATCH', body: {} })).resolves.toMatchObject({
      status: 'pending_confirmation',
    })
  })

  it('throws ApiError with status, detail and request id', async () => {
    vi.stubGlobal('fetch', respond(403, { detail: 'Operator role required' }, { 'X-Request-ID': 'req-9' }))
    const err = await rejection(api('/actions/x/execute', { method: 'POST' }))
    expect(err).toBeInstanceOf(ApiError)
    expect(err.status).toBe(403)
    expect(err.message).toBe('Operator role required')
    expect(err.requestId).toBe('req-9')
  })

  it('reports an unreachable backend as status 0', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('Failed to fetch') }))
    const err = await rejection(api('/health'))
    expect(err).toBeInstanceOf(ApiError)
    expect(err.status).toBe(0)
  })

  it('refreshes the token once on 401 and retries the same request', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response('{"detail":"Token expired"}', { status: 401 }))
      .mockResolvedValueOnce(new Response('{"status":"queued"}', { status: 202 }))
    vi.stubGlobal('fetch', fetchMock)
    await expect(api('/actions/a1/execute', { method: 'POST' })).resolves.toEqual({ status: 'queued' })
    expect(auth.refreshAccessToken).toHaveBeenCalledOnce()
    const retried = fetchMock.mock.calls[1] as unknown as [string, RequestInit]
    expect((retried[1].headers as Record<string, string>).Authorization).toBe('Bearer tok-new')
    expect(auth.expireSession).not.toHaveBeenCalled()
  })

  it('drops the session when the refresh fails', async () => {
    auth.refreshAccessToken.mockResolvedValueOnce(null)
    vi.stubGlobal('fetch', respond(401, { detail: 'Invalid authentication credentials' }))
    const err = await rejection(api('/dashboard/summary'))
    expect(err.status).toBe(401)
    expect(err.message).toMatch(/session has expired/)
    expect(auth.expireSession).toHaveBeenCalledOnce()
  })

  it('drops the session when the refreshed token is also rejected', async () => {
    const fetchMock = respond(401, { detail: 'nope' })
    vi.stubGlobal('fetch', fetchMock)
    await rejection(api('/dashboard/summary'))
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(auth.expireSession).toHaveBeenCalledOnce()
  })

  it('does not try to refresh when there was no session', async () => {
    auth.getAccessToken.mockResolvedValueOnce(null)
    vi.stubGlobal('fetch', respond(401, { detail: 'Not authenticated' }))
    const err = await rejection(api('/dashboard/summary'))
    expect(err.message).toBe('Not authenticated')
    expect(auth.refreshAccessToken).not.toHaveBeenCalled()
  })
})
