import { env } from './env'
import { expireSession, getAccessToken, refreshAccessToken } from './supabase'

export class ApiError extends Error {
  readonly status: number
  readonly requestId: string | null

  constructor(status: number, message: string, requestId: string | null) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.requestId = requestId
  }
}

type Query = Record<string, string | number | boolean | null | undefined>

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PATCH'
  query?: Query
  body?: unknown
  signal?: AbortSignal
}

export function buildUrl(path: string, query?: Query, base = env.apiBaseUrl): string {
  const params = new URLSearchParams()
  for (const [k, v] of Object.entries(query ?? {})) {
    if (v !== undefined && v !== null && v !== '') params.set(k, String(v))
  }
  const qs = params.toString()
  return `${base}/api/v1${path}${qs ? `?${qs}` : ''}`
}

// FastAPI errors are {"detail": string} or, for validation, {"detail": [{msg, loc}]}.
export function errorMessage(status: number, body: unknown): string {
  const detail = (body as { detail?: unknown } | null)?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    return detail
      .map((d) => {
        const loc = Array.isArray(d?.loc) ? d.loc.filter((p: unknown) => p !== 'query' && p !== 'body').join('.') : ''
        return loc ? `${loc}: ${d?.msg}` : String(d?.msg ?? d)
      })
      .join('; ')
  }
  if (status === 401) return 'Not signed in or session expired'
  if (status === 503) return 'The API is unavailable (database or authentication not ready)'
  if (status === 403) return 'Your account does not have the required CloudSentry role'
  if (status === 429) return 'Rate limited — wait a moment and retry'
  return `Request failed (HTTP ${status})`
}

async function send(path: string, opts: RequestOptions, token: string | null): Promise<Response> {
  const headers: Record<string, string> = { Accept: 'application/json' }
  if (token) headers.Authorization = `Bearer ${token}`
  if (opts.body !== undefined) headers['Content-Type'] = 'application/json'
  try {
    return await fetch(buildUrl(path, opts.query), {
      method: opts.method ?? 'GET',
      headers,
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
      signal: opts.signal,
    })
  } catch (e) {
    if ((e as Error).name === 'AbortError') throw e
    throw new ApiError(0, 'Cannot reach the CloudSentry API — is the backend running?', null)
  }
}

export async function api<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  // Literal condition so production builds drop the demo module entirely.
  if (import.meta.env.DEV && import.meta.env.VITE_DEMO === '1') {
    const { demoApi } = await import('./demo')
    return demoApi<T>(path, opts)
  }
  let token = await getAccessToken()
  let res = await send(path, opts, token)
  // 401 means the token was rejected before anything ran, so one retry with a
  // refreshed token is safe even for POST/PATCH. If that fails too, the session
  // is dropped and the app returns to the sign-in page.
  if (res.status === 401 && token) {
    token = await refreshAccessToken()
    if (token) res = await send(path, opts, token)
    if (!token || res.status === 401) {
      await expireSession()
      throw new ApiError(401, 'Your session has expired. Sign in again.', res.headers.get('X-Request-ID'))
    }
  }

  const requestId = res.headers.get('X-Request-ID')
  const text = await res.text()
  let body: unknown = null
  if (text) {
    try {
      body = JSON.parse(text)
    } catch {
      body = text
    }
  }
  if (!res.ok) throw new ApiError(res.status, errorMessage(res.status, body), requestId)
  return body as T
}
