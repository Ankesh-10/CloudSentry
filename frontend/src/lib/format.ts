const usdFmt = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 2 })

export function usd(v: number | null | undefined): string {
  return v === null || v === undefined || Number.isNaN(v) ? '—' : usdFmt.format(v)
}

export function num(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  return Number.isInteger(v) ? String(v) : v.toFixed(digits)
}

export function dateTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString()
}

export function relTime(iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return '—'
  const t = new Date(iso).getTime()
  if (Number.isNaN(t)) return '—'
  const s = Math.round((now - t) / 1000)
  const abs = Math.abs(s)
  const suffix = s >= 0 ? 'ago' : 'from now'
  if (abs < 60) return s >= 0 ? 'just now' : 'in a moment'
  if (abs < 3600) return `${Math.floor(abs / 60)}m ${suffix}`
  if (abs < 86400) return `${Math.floor(abs / 3600)}h ${suffix}`
  return `${Math.floor(abs / 86400)}d ${suffix}`
}

export function shortId(id: string | null | undefined): string {
  return id ? id.slice(0, 8) : '—'
}

export function humanize(s: string | null | undefined): string {
  return s ? s.replace(/_/g, ' ') : '—'
}
