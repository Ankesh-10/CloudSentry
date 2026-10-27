import { useMemo, type ReactNode } from 'react'
import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceDot,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import { useChartColors } from '../theme/ThemeProvider'
import { Tip } from './ui'

// Chart conventions (dataviz method): one y-axis per chart, 2px lines with
// round joins, area as a ~10% wash of the series hue, hairline solid grid,
// 4px rounded data-ends anchored to a square baseline, 2px surface gap between
// stacked segments, >= 8px markers with a 2px surface ring. Text always wears
// text tokens; identity comes from the swatch beside it. Every chart with two
// or more series carries a legend.

const AXIS_TICK = { fontSize: 11, fontFamily: 'var(--font-sans)' }

/* ---------- legend -------------------------------------------------------------- */

export interface LegendItem {
  label: string
  color: string
  value?: ReactNode
  dashed?: boolean
  shape?: 'line' | 'square'
}

export function Legend({ items }: { items: LegendItem[] }) {
  return (
    <div className="flex flex-wrap items-center gap-x-5 gap-y-1.5">
      {items.map((it) => (
        <span key={it.label} className="inline-flex items-center gap-2 text-[12.5px] text-fg-2">
          {it.shape === 'square' ? (
            <span className="h-2.5 w-2.5 rounded-[3px]" style={{ background: it.color }} />
          ) : (
            <span
              className="h-0 w-3.5 border-t-2"
              style={{ borderColor: it.color, borderTopStyle: it.dashed ? 'dashed' : 'solid' }}
            />
          )}
          {it.label}
          {it.value !== undefined && <span className="font-semibold text-fg tabular-nums">{it.value}</span>}
        </span>
      ))}
    </div>
  )
}

/* ---------- sparkline (plain SVG; no axes) ---------------------------------- */

export function Sparkline({ values, label, width = 96, height = 30 }: { values: number[]; label: string; width?: number; height?: number }) {
  const c = useChartColors()
  if (values.length < 2) return null
  const max = Math.max(...values)
  const min = Math.min(...values)
  const span = max - min || 1
  const pad = 5
  const pts = values.map((v, i) => [
    pad + (i / (values.length - 1)) * (width - pad * 2),
    pad + (1 - (v - min) / span) * (height - pad * 2),
  ])
  const d = pts.map(([x, y], i) => `${i ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`).join(' ')
  const [lx, ly] = pts[pts.length - 1]
  return (
    <svg width={width} height={height} role="img" aria-label={label} className="overflow-visible">
      <path d={d} fill="none" stroke={c.series1} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={lx} cy={ly} r={4} fill={c.series1} stroke={c.surface} strokeWidth={2} />
    </svg>
  )
}

/* ---------- tooltip ----------------------------------------------------------- */

interface TipEntry {
  value?: number | string
  name?: string | number
  color?: string
  stroke?: string
  dataKey?: string | number
}

export function ChartTooltip({ active, payload, label, format, labelFormat, name }: {
  active?: boolean
  payload?: TipEntry[]
  label?: string | number
  format: (v: number) => string
  labelFormat?: (l: string | number) => string
  name?: string
}) {
  if (!active || !payload?.length) return null
  return (
    <div className="min-w-36 rounded-lg border border-line bg-surface px-3 py-2.5 shadow-pop">
      <div className="text-[11.5px] text-muted">{label !== undefined ? (labelFormat ? labelFormat(label) : label) : ''}</div>
      <div className="mt-1.5 space-y-1">
        {payload.map((p, i) => (
          <div key={`${p.dataKey ?? i}`} className="flex items-center gap-2">
            <span className="h-0.5 w-3 rounded-full" style={{ background: p.stroke ?? p.color }} />
            <span className="text-[13.5px] font-semibold text-fg tabular-nums">{p.value == null ? '—' : format(Number(p.value))}</span>
            <span className="text-[12px] text-muted">{payload.length > 1 ? p.name : name ?? p.name}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

/* ---------- multi-series line/area (one y-axis) --------------------------------- */

export interface Series {
  key: string
  label: string
  color: string
  area?: boolean // ~10% wash under the line
  dashed?: boolean // comparison series only
}

export interface ChartMarker {
  x: number | string
  label: string
}

export function MultiLine<T extends object>({ data, x, series, format, tickFormat, xFormat, height = 240, time = false, markers = [], endLabels = false, markPeakOf }: {
  data: T[]
  x: keyof T & string
  series: Series[]
  format: (v: number) => string
  tickFormat?: (v: number) => string
  xFormat?: (v: string | number) => string
  height?: number
  time?: boolean // numeric epoch-ms x axis
  markers?: ChartMarker[] // vertical alert lines (e.g. anomalies)
  endLabels?: boolean // value at the end of each line
  markPeakOf?: string // series key whose maximum gets a labelled dot
}) {
  const c = useChartColors()
  const last = data.length - 1
  const peak = useMemo(() => {
    if (!markPeakOf) return null
    let best: T | null = null
    for (const row of data) {
      const v = (row as Record<string, unknown>)[markPeakOf]
      if (v != null && (!best || Number(v) > Number((best as Record<string, unknown>)[markPeakOf]))) best = row
    }
    return best
  }, [data, markPeakOf])

  const endLabel = (s: Series) =>
    endLabels
      ? (p: { x?: number | string; y?: number | string; index?: number; value?: unknown }) =>
          p.index === last && p.value != null ? (
            <text key={`end-${s.key}`} x={Number(p.x) + 8} y={Number(p.y) + 4} fill={c.text2} fontSize={11.5} fontWeight={600} fontFamily="var(--font-sans)">
              {format(Number(p.value))}
            </text>
          ) : (
            <g key={`end-${s.key}-${p.index}`} />
          )
      : undefined

  return (
    // Recharts' keyboard layer makes the svg an unnamed tab stop; the chart is
    // announced as one labelled image instead, and its figures sit beside it.
    <div style={{ height }} className="-ml-2" role="img" aria-label={`Chart: ${series.map((s) => s.label).join(' and ')}`}>
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart accessibilityLayer={false} data={data} margin={{ top: 20, right: endLabels ? 64 : 12, bottom: 0, left: 0 }}>
          <CartesianGrid stroke={c.grid} vertical={false} />
          <XAxis
            dataKey={x as string}
            type={time ? 'number' : 'category'}
            scale={time ? 'time' : 'auto'}
            domain={time ? ['dataMin', 'dataMax'] : undefined}
            tickFormatter={xFormat}
            tick={{ ...AXIS_TICK, fill: c.muted }}
            tickLine={false}
            axisLine={{ stroke: c.axis }}
            minTickGap={36}
            tickMargin={10}
          />
          <YAxis
            tickFormatter={tickFormat ?? format}
            tick={{ ...AXIS_TICK, fill: c.muted }}
            tickLine={false}
            axisLine={false}
            width={56}
            tickCount={4}
          />
          <Tooltip
            cursor={{ stroke: c.axis, strokeWidth: 1 }}
            content={<ChartTooltip format={format} labelFormat={xFormat} name={series[0]?.label} />}
          />
          {markers.map((m, i) => (
            <ReferenceLine key={i} x={m.x} stroke={c.sevHigh} strokeWidth={1.5}
                           label={{ value: m.label, position: 'top', fill: c.text2, fontSize: 11, fontFamily: 'var(--font-sans)' }} />
          ))}
          {series.map((s) =>
            s.area ? (
              <Area
                key={s.key}
                type="monotone"
                dataKey={s.key}
                name={s.label}
                stroke={s.color}
                strokeWidth={2}
                fill={s.color}
                fillOpacity={0.1}
                activeDot={{ r: 4.5, fill: s.color, stroke: c.surface, strokeWidth: 2 }}
                isAnimationActive={false}
                label={endLabel(s)}
              />
            ) : (
              <Line
                key={s.key}
                type="monotone"
                dataKey={s.key}
                name={s.label}
                stroke={s.color}
                strokeWidth={2}
                strokeDasharray={s.dashed ? '4 4' : undefined}
                strokeLinecap="round"
                dot={false}
                activeDot={{ r: 4.5, fill: s.color, stroke: c.surface, strokeWidth: 2 }}
                isAnimationActive={false}
                connectNulls
                label={endLabel(s)}
              />
            ),
          )}
          {peak && markPeakOf && (
            <ReferenceDot
              x={(peak as Record<string, unknown>)[x] as number | string}
              y={Number((peak as Record<string, unknown>)[markPeakOf])}
              r={4.5}
              fill={series.find((s) => s.key === markPeakOf)?.color ?? c.series1}
              stroke={c.surface}
              strokeWidth={2}
              label={{
                value: `Peak ${format(Number((peak as Record<string, unknown>)[markPeakOf]))}`,
                position: 'top',
                fill: c.text2,
                fontSize: 11,
                fontFamily: 'var(--font-sans)',
              }}
            />
          )}
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  )
}

// Single-series convenience wrapper (resource telemetry, etc.).
export function TrendArea<T extends object>({ data, x, y, name, format, tickFormat, xFormat, height = 240, time = false, markers = [], markPeak = true }: {
  data: T[]
  x: keyof T & string
  y: keyof T & string
  name: string
  format: (v: number) => string
  tickFormat?: (v: number) => string
  xFormat?: (v: string | number) => string
  height?: number
  time?: boolean
  markers?: ChartMarker[]
  markPeak?: boolean
}) {
  const c = useChartColors()
  return (
    <MultiLine
      data={data}
      x={x}
      series={[{ key: y, label: name, color: c.series1, area: true }]}
      format={format}
      tickFormat={tickFormat}
      xFormat={xFormat}
      height={height}
      time={time}
      markers={markers}
      markPeakOf={markPeak ? y : undefined}
    />
  )
}

/* ---------- stacked columns (plain HTML; categorical stack per bucket) ----------- */

export interface StackKey {
  key: string
  label: string
  color: string
}

export interface StackRow {
  key: string
  values: Record<string, number>
}

const niceMax = (v: number) => {
  if (v <= 4) return Math.max(1, Math.ceil(v))
  const mag = 10 ** Math.floor(Math.log10(v))
  const n = v / mag
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * mag
}

// Keys are listed bottom → top. Only the top segment gets the rounded data-end.
export function StackedColumns({ data, keys, label, height = 140, dateFormat, format = String }: {
  data: StackRow[]
  keys: StackKey[]
  label: string
  height?: number
  dateFormat?: (key: string) => string
  format?: (v: number) => string
}) {
  const totals = data.map((d) => keys.reduce((s, k) => s + (d.values[k.key] ?? 0), 0))
  const max = niceMax(Math.max(0, ...totals))
  const sum = totals.reduce((s, v) => s + v, 0)
  const fmt = dateFormat ?? ((k: string) => k)
  const ticks = [max, max / 2, 0]
  return (
    <div>
      <div className="flex" style={{ height }}>
        <div className="flex w-9 shrink-0 flex-col justify-between pr-2 text-right text-[11px] leading-none text-muted tabular-nums" aria-hidden>
          {ticks.map((t) => <span key={t} className="-translate-y-1/2 first:translate-y-0 last:translate-y-0">{format(Math.round(t * 10) / 10)}</span>)}
        </div>
        <div className="relative min-w-0 flex-1" role="img" aria-label={`${label}: ${sum} in total over ${data.length} periods`}>
          <div className="pointer-events-none absolute inset-0 flex flex-col justify-between" aria-hidden>
            <div className="border-t border-[var(--chart-grid)]" />
            <div className="border-t border-[var(--chart-grid)]" />
            <div className="border-t border-[var(--chart-axis)]" />
          </div>
          <div className="relative flex h-full items-end gap-[2px]">
            {data.map((d, i) => {
              const total = totals[i]
              const present = keys.filter((k) => (d.values[k.key] ?? 0) > 0)
              const top = present[present.length - 1]?.key
              return (
                <Tip
                  key={d.key}
                  content={
                    <div>
                      <div className="mb-1 text-[11.5px] text-muted">{fmt(d.key)}</div>
                      {keys.slice().reverse().map((k) => (
                        <div key={k.key} className="flex items-center gap-2">
                          <span className="h-2 w-2 rounded-[2px]" style={{ background: k.color }} />
                          <span className="flex-1">{k.label}</span>
                          <b className="text-fg tabular-nums">{format(d.values[k.key] ?? 0)}</b>
                        </div>
                      ))}
                      <div className="mt-1 border-t border-line pt-1 text-[12px]">Total <b className="text-fg">{format(total)}</b></div>
                    </div>
                  }
                >
                  <div className="group flex h-full min-w-0 flex-1 cursor-default items-end justify-center hover:bg-subtle/60">
                    <div className="flex w-full max-w-[24px] flex-col-reverse gap-[2px]" style={{ height: `${(total / max) * 100}%` }}>
                      {present.map((k) => (
                        <div
                          key={k.key}
                          className={k.key === top ? 'rounded-t-[4px]' : ''}
                          style={{ background: k.color, flexGrow: d.values[k.key], flexBasis: 0, minHeight: 2 }}
                        />
                      ))}
                    </div>
                  </div>
                </Tip>
              )
            })}
          </div>
        </div>
      </div>
      {data.length > 0 && (
        <div className="mt-2 ml-9 flex justify-between text-[11px] text-muted">
          <span>{fmt(data[0].key)}</span>
          {data.length > 2 && <span>{fmt(data[Math.floor(data.length / 2)].key)}</span>}
          <span>{fmt(data[data.length - 1].key)}</span>
        </div>
      )}
    </div>
  )
}

/* ---------- single-series columns ------------------------------------------------ */

export function Columns({ data, label, height = 110, format = String, dateFormat, color }: {
  data: { key: string; value: number }[]
  label: string
  height?: number
  format?: (v: number) => string
  dateFormat?: (key: string) => string
  color?: string
}) {
  const c = useChartColors()
  return (
    <StackedColumns
      data={data.map((d) => ({ key: d.key, values: { v: d.value } }))}
      keys={[{ key: 'v', label, color: color ?? c.series1 }]}
      label={label}
      height={height}
      format={format}
      dateFormat={dateFormat}
    />
  )
}

/* ---------- part-to-whole: one stacked bar + legend (plain HTML) ----------------- */

export interface Segment {
  key: string
  label: string
  value: number
  color: string
}

export function StackedBar({ segments, total, emptyLabel = 'None' }: { segments: Segment[]; total?: number; emptyLabel?: string }) {
  const sum = total ?? segments.reduce((s, x) => s + x.value, 0)
  const visible = segments.filter((s) => s.value > 0)
  return (
    <div>
      <div className="flex h-2.5 w-full gap-[2px] overflow-hidden rounded-full bg-subtle" role="img"
           aria-label={segments.map((s) => `${s.label} ${s.value}`).join(', ')}>
        {sum === 0 ? null : visible.map((s) => (
          <Tip key={s.key} content={<span><b className="text-fg">{s.value}</b> {s.label.toLowerCase()} ({Math.round((s.value / sum) * 100)}%)</span>}>
            <div className="h-full first:rounded-l-full last:rounded-r-full" style={{ width: `${(s.value / sum) * 100}%`, background: s.color }} />
          </Tip>
        ))}
      </div>
      <div className="mt-3">
        {sum === 0
          ? <span className="text-[12.5px] text-muted">{emptyLabel}</span>
          : <Legend items={segments.map((s) => ({ label: s.label, color: s.color, value: s.value, shape: 'square' }))} />}
      </div>
    </div>
  )
}

/* ---------- magnitude by category: labelled horizontal bars (plain HTML) -------- */

export function BarList({ rows, format = String, onSelect }: {
  rows: { key: string; label: ReactNode; value: number; hint?: string }[]
  format?: (v: number) => string
  onSelect?: (key: string) => void
}) {
  const max = Math.max(1, ...rows.map((r) => r.value))
  return (
    <div className="space-y-1">
      {rows.map((r) => {
        const inner = (
          <>
            <div className="flex items-baseline justify-between gap-3">
              <span className="truncate text-[13px] text-fg-2 group-hover:text-fg">{r.label}</span>
              <span className="text-[13px] font-semibold text-fg tabular-nums">{format(r.value)}</span>
            </div>
            <div className="mt-1.5 h-1.5 rounded-full bg-subtle">
              <div className="h-full rounded-full bg-series-1" style={{ width: `${Math.max((r.value / max) * 100, r.value ? 2 : 0)}%` }} />
            </div>
          </>
        )
        return onSelect ? (
          <button key={r.key} type="button" title={r.hint} onClick={() => onSelect(r.key)}
                  className="group block w-full rounded-md px-2 py-1.5 text-left hover:bg-subtle">
            {inner}
          </button>
        ) : (
          <div key={r.key} title={r.hint} className="group px-2 py-1.5">{inner}</div>
        )
      })}
    </div>
  )
}

/* ---------- helpers ------------------------------------------------------------------ */

const DAY = 86400_000

function utcDayStart(daysAgo: number) {
  const today = new Date()
  today.setUTCHours(0, 0, 0, 0)
  return today.getTime() - daysAgo * DAY
}

// Counts per UTC day for the last `days` days (oldest first).
export function dailyBuckets<T>(items: T[], getTime: (t: T) => string | null | undefined, days: number) {
  const t0 = utcDayStart(days - 1)
  const out: { key: string; value: number }[] = []
  for (let i = 0; i < days; i++) out.push({ key: new Date(t0 + i * DAY).toISOString().slice(0, 10), value: 0 })
  for (const it of items) {
    const ts = getTime(it)
    if (!ts) continue
    const i = Math.floor((new Date(ts).getTime() - t0) / DAY)
    if (i >= 0 && i < days) out[i].value += 1
  }
  return out
}

// Per-day counts split by a category (oldest first), for StackedColumns.
export function dailyStacks<T>(items: T[], getTime: (t: T) => string | null | undefined, getKey: (t: T) => string | null | undefined, days: number): StackRow[] {
  const t0 = utcDayStart(days - 1)
  const out: StackRow[] = Array.from({ length: days }, (_, i) => ({ key: new Date(t0 + i * DAY).toISOString().slice(0, 10), values: {} }))
  for (const it of items) {
    const ts = getTime(it)
    const k = getKey(it)
    if (!ts || !k) continue
    const i = Math.floor((new Date(ts).getTime() - t0) / DAY)
    if (i >= 0 && i < days) out[i].values[k] = (out[i].values[k] ?? 0) + 1
  }
  return out
}

export const shortDay = (key: string | number) =>
  new Date(`${key}T00:00:00Z`).toLocaleDateString([], { month: 'short', day: 'numeric', timeZone: 'UTC' })
