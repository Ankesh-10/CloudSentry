import { useMemo } from 'react'

import { useActions, useAnomalies, useCostTrend, useDashboardSummary } from '../hooks/queries'
import { usd } from '../lib/format'
import type { Action, Anomaly } from '../lib/types'
import { useChartColors } from '../theme/ThemeProvider'
import { Legend, MultiLine, StackedColumns, dailyStacks, shortDay } from './charts'
import { Card, DeltaLabel, Empty, ErrorBox, MiniStat, Skeleton, type Delta } from './ui'

// Shared analytical panels: spend vs the previous period, savings identified
// vs realized, and anomaly detections by severity. Each is one chart on one
// y-axis with a legend, plus a row of supporting figures underneath.

const DAY = 86400_000
export const compactUsd = (v: number) => (Math.abs(v) >= 1000 ? `$${(v / 1000).toFixed(1)}K` : `$${Math.round(v)}`)

function pctDelta(cur: number, prev: number, vs: string, upIsGood: boolean): Delta | null {
  if (!prev) return null
  const pct = (cur - prev) / prev
  if (Math.abs(pct) < 0.005) return { text: '0%', direction: 'flat', good: null, vs }
  const up = pct > 0
  return { text: `${Math.abs(pct * 100).toFixed(1)}%`, direction: up ? 'up' : 'down', good: up === upIsGood, vs }
}

export function duration(ms: number | null): string {
  if (ms == null || !Number.isFinite(ms)) return '—'
  const h = ms / 3_600_000
  if (h < 1) return `${Math.max(1, Math.round(ms / 60_000))} min`
  if (h < 48) return `${h.toFixed(h < 10 ? 1 : 0)} h`
  return `${(h / 24).toFixed(1)} d`
}

function median(xs: number[]): number | null {
  if (!xs.length) return null
  const s = [...xs].sort((a, b) => a - b)
  const m = Math.floor(s.length / 2)
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2
}

function windowStart(days: number) {
  const d = new Date()
  d.setUTCHours(0, 0, 0, 0)
  return d.getTime() - (days - 1) * DAY
}

/* ---------- spend vs previous period --------------------------------------------- */

export function SpendPanel({ range, className = '', index = 0 }: { range: number; className?: string; index?: number }) {
  const c = useChartColors()
  // Two periods where the API allows (max 90 days) so there is something to compare.
  const fetchDays = Math.min(range * 2, 90)
  const { data, error, isLoading } = useCostTrend(fetchDays)
  const summary = useDashboardSummary()

  const m = useMemo(() => {
    const rows = data ?? []
    const current = rows.slice(-range)
    const previous = fetchDays >= range * 2 ? rows.slice(-range * 2, -range) : []
    const hasPrev = previous.length === range
    const total = current.reduce((s, r) => s + r.cost_usd, 0)
    const prevTotal = previous.reduce((s, r) => s + r.cost_usd, 0)
    const series = current.map((r, i) => ({ date: r.date, current: r.cost_usd, previous: hasPrev ? previous[i].cost_usd : null }))
    const peak = current.reduce<(typeof current)[number] | null>((b, r) => (!b || r.cost_usd > b.cost_usd ? r : b), null)
    return {
      series,
      total,
      hasPrev,
      prevTotal,
      avg: current.length ? total / current.length : 0,
      peak,
      delta: hasPrev ? pctDelta(total, prevTotal, `vs previous ${range} days`, false) : null,
    }
  }, [data, range, fetchDays])

  const now = new Date()
  const dayOfMonth = now.getUTCDate()
  const daysInMonth = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() + 1, 0)).getUTCDate()
  const mtd = summary.data?.month_to_date_cost_estimated_usd
  const projected = mtd != null ? (mtd / dayOfMonth) * daysInMonth : null

  return (
    <Card title="Estimated spend" subtitle={`Daily cost, last ${range} days${m.hasPrev ? ' against the period before' : ''}`} className={className} index={index}>
      {error ? <ErrorBox error={error} /> : isLoading ? (
        <><Skeleton className="h-12 w-56" /><Skeleton className="mt-6 h-60" /></>
      ) : !m.series.length ? (
        <Empty title="No cost data yet">The cost job runs after discovery and telemetry have collected data.</Empty>
      ) : (
        <>
          <div className="flex flex-wrap items-end justify-between gap-4">
            <div>
              <data value={m.total.toFixed(2)} className="figure block text-[clamp(2.25rem,4vw,3rem)] text-fg">{usd(m.total)}</data>
              <div className="mt-2 min-h-5">{m.delta && <DeltaLabel delta={m.delta} />}</div>
            </div>
            <Legend
              items={[
                { label: `Last ${range} days`, color: c.series1 },
                ...(m.hasPrev ? [{ label: 'Previous period', color: c.prev, dashed: true }] : []),
              ]}
            />
          </div>
          <div className="mt-4">
            <MultiLine
              data={m.series}
              x="date"
              series={[
                ...(m.hasPrev ? [{ key: 'previous', label: 'Previous period', color: c.prev, dashed: true }] : []),
                { key: 'current', label: `Last ${range} days`, color: c.series1, area: true },
              ]}
              format={usd}
              tickFormat={compactUsd}
              xFormat={shortDay}
              height={240}
              markPeakOf="current"
            />
          </div>
          <div className="mt-5 grid grid-cols-2 gap-x-6 gap-y-4 border-t border-line pt-5 sm:grid-cols-4">
            <MiniStat label="Average per day" value={usd(m.avg)} hint={m.hasPrev ? `was ${usd(m.prevTotal / range)}` : undefined} />
            <MiniStat label="Peak day" value={m.peak ? usd(m.peak.cost_usd) : '—'} hint={m.peak ? shortDay(m.peak.date) : undefined} />
            <MiniStat label="Month to date" value={mtd != null ? usd(mtd) : '—'} hint={`Day ${dayOfMonth} of ${daysInMonth}`} />
            <MiniStat label="Projected month-end" value={projected != null ? usd(projected) : '—'} hint="At the current daily pace" />
          </div>
        </>
      )}
    </Card>
  )
}

/* ---------- savings identified vs realized ----------------------------------------- */

const OPEN = new Set(['pending', 'pending_approval', 'approved'])

export function savingsSeries(actions: Action[], range: number) {
  const t0 = windowStart(range)
  const identified = new Array(range).fill(0)
  const realized = new Array(range).fill(0)
  const idx = (iso: string | null) => (iso ? Math.floor((new Date(iso).getTime() - t0) / DAY) : -1)
  for (const a of actions) {
    const v = a.estimated_savings_usd ?? 0
    if (!v || a.status === 'rejected') continue
    const i = idx(a.created_at)
    if (i >= 0 && i < range) identified[i] += v
    if (a.status === 'completed' && !a.dry_run) {
      const j = idx(a.executed_at ?? a.verified_at)
      if (j >= 0 && j < range) realized[j] += v
    }
  }
  let ci = 0
  let cr = 0
  return Array.from({ length: range }, (_, i) => {
    ci += identified[i]
    cr += realized[i]
    return { date: new Date(t0 + i * DAY).toISOString().slice(0, 10), identified: Math.round(ci * 100) / 100, realized: Math.round(cr * 100) / 100 }
  })
}

export function SavingsPanel({ range, className = '', index = 0 }: { range: number; className?: string; index?: number }) {
  const c = useChartColors()
  const { data, error, isLoading } = useActions({ limit: 500 })

  const m = useMemo(() => {
    const actions = data ?? []
    const series = savingsSeries(actions, range)
    const last = series[series.length - 1] ?? { identified: 0, realized: 0 }
    const t0 = windowStart(range)
    const inWindow = (a: Action) => a.created_at && new Date(a.created_at).getTime() >= t0
    const sum = (f: (a: Action) => boolean) => actions.filter(f).reduce((s, a) => s + (a.estimated_savings_usd ?? 0), 0)
    return {
      series,
      identified: last.identified,
      realized: last.realized,
      awaiting: sum((a) => OPEN.has(a.status ?? '')),
      dryRun: sum((a) => a.status === 'completed' && !!a.dry_run && !!inWindow(a)),
      failed: actions.filter((a) => (a.status === 'failed' || a.status === 'rolled_back') && inWindow(a)).length,
    }
  }, [data, range])

  const capture = m.identified ? Math.round((m.realized / m.identified) * 100) : null

  return (
    <Card title="Savings identified vs realized" subtitle={`Monthly savings added over the last ${range} days, cumulative estimate`} className={className} index={index}>
      {error ? <ErrorBox error={error} /> : isLoading ? <Skeleton className="h-72" /> : (
        <>
          <Legend
            items={[
              { label: 'Identified', color: c.series1, value: usd(m.identified) },
              { label: 'Realized (live)', color: c.series2, value: usd(m.realized) },
            ]}
          />
          <div className="mt-3">
            <MultiLine
              data={m.series}
              x="date"
              series={[
                { key: 'identified', label: 'Identified', color: c.series1, area: true },
                { key: 'realized', label: 'Realized (live)', color: c.series2, area: true },
              ]}
              format={usd}
              tickFormat={compactUsd}
              xFormat={shortDay}
              height={210}
              endLabels
            />
          </div>
          <div className="mt-5 grid grid-cols-2 gap-x-6 gap-y-4 border-t border-line pt-5">
            <MiniStat label="Capture rate" value={capture == null ? '—' : `${capture}%`} hint="Realized ÷ identified" />
            <MiniStat label="Awaiting decision" value={usd(m.awaiting)} hint="Open proposals" />
            <MiniStat label="Dry-run validated" value={usd(m.dryRun)} hint="Would save if run live" />
            <MiniStat label="Failed or rolled back" value={m.failed} hint="In this window" />
          </div>
        </>
      )}
    </Card>
  )
}

/* ---------- anomaly detections by severity ------------------------------------------ */

export function useSeverityKeys() {
  const c = useChartColors()
  // Bottom → top: the most severe sits on top, in the darkest step of the ramp.
  return [
    { key: 'LOW', label: 'Low', color: c.sevLow },
    { key: 'MEDIUM', label: 'Medium', color: c.sevMed },
    { key: 'HIGH', label: 'High', color: c.sevHigh },
  ]
}

export function anomalyStats(anomalies: Anomaly[], range: number) {
  const t0 = windowStart(range)
  const inWin = anomalies.filter((a) => a.detected_at && new Date(a.detected_at).getTime() >= t0)
  const resolveMs = inWin
    .filter((a) => a.status === 'resolved' && a.resolved_at && a.detected_at)
    .map((a) => new Date(a.resolved_at!).getTime() - new Date(a.detected_at!).getTime())
    .filter((ms) => ms >= 0)
  const closed = inWin.filter((a) => a.status === 'resolved' || a.status === 'false_positive')
  const fp = inWin.filter((a) => a.status === 'false_positive').length
  const sev = { HIGH: 0, MEDIUM: 0, LOW: 0 } as Record<string, number>
  for (const a of inWin) if (a.severity) sev[a.severity] += 1
  return {
    total: inWin.length,
    perDay: inWin.length / range,
    medianResolve: median(resolveMs),
    fpRate: closed.length ? Math.round((fp / closed.length) * 100) : null,
    open: inWin.filter((a) => a.status === 'active').length,
    sev,
  }
}

export function AnomalyTimelinePanel({ range, className = '', index = 0 }: { range: number; className?: string; index?: number }) {
  const keys = useSeverityKeys()
  const { data, error, isLoading } = useAnomalies({ limit: 500 })
  const rows = useMemo(() => dailyStacks(data ?? [], (a) => a.detected_at, (a) => a.severity, range), [data, range])
  const s = useMemo(() => anomalyStats(data ?? [], range), [data, range])

  return (
    <Card title="Anomalies over time" subtitle={`Detections per day by severity, last ${range} days`} className={className} index={index}>
      {error ? <ErrorBox error={error} /> : isLoading ? <Skeleton className="h-72" /> : (
        <>
          <Legend items={keys.slice().reverse().map((k) => ({ label: k.label, color: k.color, value: s.sev[k.key], shape: 'square' as const }))} />
          <div className="mt-4">
            <StackedColumns data={rows} keys={keys} label="Anomalies detected per day by severity" height={186} dateFormat={shortDay} />
          </div>
          <div className="mt-5 grid grid-cols-2 gap-x-6 gap-y-4 border-t border-line pt-5">
            <MiniStat label="Detected" value={s.total} hint={`${s.perDay.toFixed(1)} per day`} />
            <MiniStat label="Still open" value={s.open} hint="Detected in window" />
            <MiniStat label="Median time to resolve" value={duration(s.medianResolve)} />
            <MiniStat label="False-positive rate" value={s.fpRate == null ? '—' : `${s.fpRate}%`} hint="Of closed anomalies" />
          </div>
        </>
      )}
    </Card>
  )
}
