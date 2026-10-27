// Build-time configuration. VITE_* values are public (compiled into the bundle).
export const env = {
  supabaseUrl: (import.meta.env.VITE_SUPABASE_URL ?? '').trim(),
  // A publishable key (sb_publishable_…) or the legacy anon JWT; both are public.
  supabaseAnonKey: (import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY || import.meta.env.VITE_SUPABASE_ANON_KEY || '').trim(),
  // Empty = same origin (dev proxy). Trailing slash stripped so paths join cleanly.
  apiBaseUrl: (import.meta.env.VITE_API_BASE_URL ?? '').trim().replace(/\/+$/, ''),
}

// Dev-only sample data mode: `VITE_DEMO=1 npm run dev`. Always false in builds.
export const isDemo = import.meta.env.DEV && import.meta.env.VITE_DEMO === '1'

export function missingConfig(): string[] {
  if (isDemo) return []
  const missing: string[] = []
  if (!env.supabaseUrl) missing.push('VITE_SUPABASE_URL')
  if (!env.supabaseAnonKey) missing.push('VITE_SUPABASE_PUBLISHABLE_KEY (or legacy VITE_SUPABASE_ANON_KEY)')
  return missing
}
