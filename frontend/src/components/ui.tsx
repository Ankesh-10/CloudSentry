import { ArrowRightIcon, IconContext } from '@phosphor-icons/react'
import * as RT from '@radix-ui/react-tooltip'
import { useEffect, useRef, useState, type ButtonHTMLAttributes, type CSSProperties, type ReactNode } from 'react'
import { Link } from 'react-router-dom'

import { ApiError } from '../lib/api'
import { humanize } from '../lib/format'

/* ---------- motion ------------------------------------------------------------ */

// Scroll entry: marks the element shown once it enters the viewport; the CSS
// in index.css does the fade/rise (and skips it under reduced motion).
export function useReveal<T extends HTMLElement>() {
  const ref = useRef<T>(null)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    if (typeof IntersectionObserver === 'undefined') {
      el.dataset.shown = ''
      return
    }
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          el.dataset.shown = ''
          io.disconnect()
        }
      },
      { rootMargin: '0px 0px -32px 0px' },
    )
    io.observe(el)
    return () => io.disconnect()
  }, [])
  return ref
}

/* ---------- layout ------------------------------------------------------------ */

// Bento grid: separate cards on the canvas, never shared borders.
export function Grid({ className = '', children }: { className?: string; children: ReactNode }) {
  return <div className={`grid grid-cols-1 gap-4 lg:gap-5 ${className}`}>{children}</div>
}

// A bordered white surface that fades in on scroll. `index` staggers siblings.
export function Panel({ className = '', index = 0, children, as: Tag = 'section' }: {
  className?: string
  index?: number
  children: ReactNode
  as?: 'section' | 'div'
}) {
  const ref = useReveal<HTMLElement>()
  return (
    <Tag
      ref={ref as never}
      style={{ '--index': index } as CSSProperties}
      className={`reveal min-w-0 rounded-xl border border-line bg-surface hover:shadow-hover ${className}`}
    >
      {children}
    </Tag>
  )
}

export function Card({ title, subtitle, actions, children, className = '', flush = false, index = 0 }: {
  title?: ReactNode
  subtitle?: ReactNode
  actions?: ReactNode
  children: ReactNode
  className?: string
  flush?: boolean // no body padding (tables, lists)
  index?: number
}) {
  return (
    <Panel className={className} index={index}>
      {(title || actions) && (
        <header className={`flex items-start justify-between gap-4 px-6 pt-5 ${flush ? 'pb-4' : 'pb-1'}`}>
          <div className="min-w-0">
            <h2 className="truncate text-[14.5px] font-semibold tracking-[-0.01em] text-fg">{title}</h2>
            {subtitle && <p className="mt-0.5 truncate text-[12.5px] text-muted">{subtitle}</p>}
          </div>
          {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className={flush ? '' : 'px-6 pt-4 pb-6'}>{children}</div>
    </Panel>
  )
}

// Each page names itself in the browser tab and for screen readers (WCAG 2.4.2).
export function useDocumentTitle(title: string) {
  useEffect(() => {
    document.title = title ? `${title} · CloudSentry` : 'CloudSentry'
  }, [title])
}

export function PageHeader({ title, subtitle, actions, kicker }: { title: string; subtitle?: ReactNode; actions?: ReactNode; kicker?: string }) {
  useDocumentTitle(title)
  return (
    <div className="mb-8 flex flex-wrap items-end justify-between gap-x-8 gap-y-4 pt-2">
      <div className="min-w-0 max-w-3xl">
        {kicker && <div className="eyebrow mb-3 text-muted">{kicker}</div>}
        <h1 className="display text-[clamp(2.1rem,3.6vw,2.9rem)] wrap-anywhere text-fg">{title}</h1>
        {subtitle && <p className="mt-2.5 text-[14.5px] text-muted">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  )
}

/* ---------- figures ----------------------------------------------------------- */

export interface Delta {
  text: string
  direction: 'up' | 'down' | 'flat'
  good: boolean | null // null = neutral (no judgement)
  vs: string
}

export function DeltaLabel({ delta }: { delta: Delta }) {
  const glyph = delta.direction === 'up' ? '↑' : delta.direction === 'down' ? '↓' : '→'
  const color = delta.good === false ? 'text-accent' : delta.good ? 'text-success' : 'text-fg-2'
  return (
    <span className="inline-flex items-center gap-1.5 text-[12.5px]">
      <span className={`font-semibold ${color}`}>
        {glyph} {delta.text}
      </span>
      <span className="text-muted">{delta.vs}</span>
    </span>
  )
}

export function Stat({ label, value, delta, trend, hint, alert = false }: {
  label: string
  value: ReactNode
  delta?: Delta | null
  trend?: ReactNode
  hint?: ReactNode
  alert?: boolean
}) {
  return (
    <div className="flex min-h-[124px] flex-col justify-between px-6 py-5">
      <div className="flex items-center justify-between gap-2 text-[12.5px] text-muted">
        <span>{label}</span>
        {alert && <Badge tone="critical">High</Badge>}
      </div>
      <div className="mt-3 flex items-end justify-between gap-3">
        <div className={`figure text-[2rem] ${alert ? 'text-accent' : 'text-fg'}`}>{value}</div>
        {trend && <div className="mb-0.5 shrink-0">{trend}</div>}
      </div>
      <div className="mt-2 min-h-5">
        {delta ? <DeltaLabel delta={delta} /> : hint ? <span className="text-[12.5px] text-muted">{hint}</span> : null}
      </div>
    </div>
  )
}

// A compact label/value pair for rows of secondary figures under a chart.
export function MiniStat({ label, value, hint }: { label: string; value: ReactNode; hint?: ReactNode }) {
  return (
    <div className="min-w-0">
      <div className="truncate text-[12px] text-muted">{label}</div>
      <div className="mt-1 text-[16px] font-semibold tracking-[-0.01em] text-fg tabular-nums">{value}</div>
      {hint && <div className="mt-0.5 truncate text-[11.5px] text-muted">{hint}</div>}
    </div>
  )
}

/* ---------- status ------------------------------------------------------------ */

export type Tone = 'neutral' | 'accent' | 'good' | 'warning' | 'serious' | 'critical' | 'rehearsal'

// Muted pastel per tone: tinted background + readable text. The word always
// travels with the colour.
const PASTEL: Record<Tone, string> = {
  critical: 'bg-accent-soft text-accent',
  serious: 'bg-warn-soft text-warn',
  warning: 'bg-warn-soft text-warn',
  good: 'bg-success-soft text-success',
  accent: 'bg-info-soft text-info',
  rehearsal: 'bg-rehearsal-soft text-rehearsal',
  neutral: 'bg-subtle text-muted',
}
const DOT: Record<Tone, string> = {
  critical: 'bg-accent',
  serious: 'bg-warn',
  warning: 'bg-warn',
  good: 'bg-success',
  accent: 'bg-info',
  rehearsal: 'bg-rehearsal',
  neutral: 'bg-muted',
}

export function Dot({ tone, pulse = false }: { tone: Tone; pulse?: boolean }) {
  return (
    <span
      aria-hidden
      className={`inline-block h-1.5 w-1.5 shrink-0 rounded-full ${DOT[tone]} ${pulse ? '[animation:pulse-soft_1.4s_ease-in-out_infinite]' : ''}`}
    />
  )
}

export function Badge({ tone = 'neutral', children, pulse }: { tone?: Tone; children: ReactNode; pulse?: boolean }) {
  return (
    <span className={`eyebrow inline-flex items-center gap-1.5 rounded-full px-2 py-[3px] text-[10.5px] whitespace-nowrap ${PASTEL[tone]}`}>
      <Dot tone={tone} pulse={pulse} />
      {children}
    </span>
  )
}

export function Tag({ children, mono = true }: { children: ReactNode; mono?: boolean }) {
  return (
    <span className={`inline-flex items-center rounded-[4px] bg-subtle px-1.5 py-px text-[11.5px] whitespace-nowrap text-fg-2 ${mono ? 'font-mono' : ''}`}>
      {children}
    </span>
  )
}

const STATUS_TONE: Record<string, Tone> = {
  // anomalies
  active: 'accent',
  resolved: 'good',
  false_positive: 'neutral',
  // actions
  pending: 'warning',
  pending_approval: 'warning',
  approved: 'accent',
  executing: 'accent',
  completed: 'good',
  failed: 'critical',
  rejected: 'neutral',
  rolled_back: 'neutral',
  expired: 'neutral',
  // resources
  running: 'good',
  available: 'good',
  'in-use': 'good',
  stopped: 'neutral',
  stopping: 'warning',
  // health
  ok: 'good',
  success: 'good',
  degraded: 'warning',
  unhealthy: 'critical',
  error: 'critical',
  // severity
  HIGH: 'critical',
  MEDIUM: 'serious',
  LOW: 'accent',
}

export function statusTone(value: string | null | undefined): Tone {
  return (value && STATUS_TONE[value]) || 'neutral'
}

export function sentence(s: string | null | undefined): string {
  const h = humanize(s)
  return h === '—' ? h : h.charAt(0).toUpperCase() + h.slice(1).toLowerCase()
}

export function StatusBadge({ value }: { value: string | null | undefined }) {
  if (!value) return <span className="text-muted">—</span>
  return (
    <Badge tone={statusTone(value)} pulse={value === 'executing'}>
      {humanize(value)}
    </Badge>
  )
}

/* ---------- controls ---------------------------------------------------------- */

type Variant = 'primary' | 'secondary' | 'danger' | 'ghost' | 'danger-outline'
const VARIANTS: Record<Variant, string> = {
  primary: 'border border-primary bg-primary text-on-primary hover:border-primary-hover hover:bg-primary-hover',
  secondary: 'border border-line bg-surface text-fg hover:border-line-strong hover:bg-subtle',
  danger: 'border border-accent bg-accent text-surface hover:opacity-90',
  'danger-outline': 'border border-accent/35 bg-surface text-accent hover:bg-accent-soft',
  ghost: 'border border-transparent text-fg-2 hover:bg-subtle hover:text-fg',
}

export function Button({ variant = 'secondary', size = 'md', className = '', ...props }: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: Variant
  size?: 'sm' | 'md'
}) {
  const sz = size === 'sm' ? 'h-8 px-3 text-[12.5px]' : 'h-9 px-4 text-[13.5px]'
  return (
    <button
      type="button"
      {...props}
      className={`inline-flex shrink-0 items-center justify-center gap-1.5 rounded-md font-medium whitespace-nowrap transition-[background-color,border-color,transform,opacity] duration-150 active:scale-[0.98] disabled:pointer-events-none disabled:opacity-45 ${sz} ${VARIANTS[variant]} ${className}`}
    />
  )
}

// Legacy two-step button kept for compatibility; dialogs are preferred.
export function ConfirmButton({ onConfirm, children, confirmLabel = 'Confirm?', ...props }: Omit<
  Parameters<typeof Button>[0],
  'onClick'
> & { onConfirm: () => void; confirmLabel?: string }) {
  const [armed, setArmed] = useState(false)
  useEffect(() => {
    if (!armed) return
    const t = setTimeout(() => setArmed(false), 4000)
    return () => clearTimeout(t)
  }, [armed])
  return (
    <Button
      {...props}
      variant={armed ? 'danger' : props.variant}
      onClick={() => {
        if (armed) {
          setArmed(false)
          onConfirm()
        } else setArmed(true)
      }}
    >
      {armed ? confirmLabel : children}
    </Button>
  )
}

export function Segmented<T extends string | number>({ value, onChange, options, label }: {
  value: T
  onChange: (v: T) => void
  options: { value: T; label: ReactNode; count?: number }[]
  label: string
}) {
  return (
    <div role="radiogroup" aria-label={label} className="inline-flex flex-wrap gap-0.5 rounded-lg border border-line bg-subtle p-0.5">
      {options.map((o) => {
        const on = o.value === value
        return (
          <button
            key={String(o.value)}
            type="button"
            role="radio"
            aria-checked={on}
            onClick={() => onChange(o.value)}
            className={`inline-flex h-7 items-center gap-1.5 rounded-md px-2.5 text-[12.5px] font-medium transition-colors ${
              on ? 'bg-surface text-fg shadow-hover ring-1 ring-line' : 'text-muted hover:text-fg'
            }`}
          >
            {o.label}
            {o.count !== undefined && <span className="text-muted tabular-nums">{o.count}</span>}
          </button>
        )
      })}
    </div>
  )
}

export function TooltipProvider({ children }: { children: ReactNode }) {
  return (
    <IconContext.Provider value={{ weight: 'bold', size: 16 }}>
      <RT.Provider delayDuration={200}>{children}</RT.Provider>
    </IconContext.Provider>
  )
}

export function Tip({ content, children, side = 'top' }: { content: ReactNode; children: ReactNode; side?: 'top' | 'bottom' | 'left' | 'right' }) {
  if (!content) return <>{children}</>
  return (
    <RT.Root>
      <RT.Trigger asChild>{children}</RT.Trigger>
      <RT.Portal>
        <RT.Content
          side={side}
          sideOffset={6}
          className="z-50 max-w-xs rounded-md border border-line bg-surface px-2.5 py-1.5 text-[12.5px] text-fg-2 shadow-pop [animation:fade-in_100ms_ease-out]"
        >
          {content}
        </RT.Content>
      </RT.Portal>
    </RT.Root>
  )
}

/* ---------- states ------------------------------------------------------------ */

export function Skeleton({ className = '' }: { className?: string }) {
  return <div className={`rounded-md bg-subtle [animation:shimmer_1.6s_ease-in-out_infinite] ${className}`} aria-hidden />
}

export function Loading({ label = 'Loading' }: { label?: string }) {
  return (
    <div role="status" aria-label={label} className="flex items-center gap-2 py-6 text-[13px] text-muted">
      <span className="inline-block h-1.5 w-1.5 rounded-full bg-muted [animation:pulse-soft_1s_ease-in-out_infinite]" />
      {label}…
    </div>
  )
}

export function Empty({ icon, title, children, action }: { icon?: ReactNode; title?: ReactNode; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-start justify-center px-6 py-10">
      {icon && <div className="mb-3 text-muted">{icon}</div>}
      <div className="text-[14px] font-medium text-fg">{title ?? 'No data'}</div>
      {children && <div className="mt-1 max-w-md text-[13px] text-muted">{children}</div>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  )
}

export function ErrorBox({ error, title }: { error: unknown; title?: string }) {
  if (!error) return null
  const msg = error instanceof Error ? error.message : String(error)
  const rid = error instanceof ApiError ? error.requestId : null
  return (
    <div role="alert" className="rounded-lg bg-accent-soft px-4 py-3 text-[13px] text-fg-2">
      <div className="mb-0.5 font-medium text-accent">{title ?? 'Request failed'}</div>
      {msg}
      {rid && <div className="mt-1 text-[11.5px] text-muted">Request <samp className="font-mono">{rid}</samp></div>}
    </div>
  )
}

type NoticeTone = 'neutral' | 'warning' | 'good' | 'accent'
const NOTICE_ALIAS: Record<string, NoticeTone> = { amber: 'warning', green: 'good', blue: 'accent' }
const NOTICE_BG: Record<NoticeTone, string> = { neutral: 'bg-subtle', warning: 'bg-warn-soft', good: 'bg-success-soft', accent: 'bg-info-soft' }

export function Notice({ tone = 'neutral', children }: { tone?: NoticeTone | 'amber' | 'green' | 'blue'; children: ReactNode }) {
  const t: NoticeTone = NOTICE_ALIAS[tone] ?? (tone as NoticeTone)
  return (
    <div className={`flex items-start gap-2.5 rounded-lg px-4 py-3 text-[13px] text-fg-2 ${NOTICE_BG[t]}`}>
      <span className="mt-[7px]"><Dot tone={t} /></span>
      <div>{children}</div>
    </div>
  )
}

/* ---------- data ---------------------------------------------------------------- */

export function Table({ head, children }: { head: ReactNode; children: ReactNode }) {
  return (
    <div className="overflow-x-auto">
      <table className="min-w-full border-collapse">
        <thead>
          <tr>{head}</tr>
        </thead>
        <tbody className="divide-y divide-line">{children}</tbody>
      </table>
    </div>
  )
}

export function Json({ value }: { value: unknown }) {
  if (value === null || value === undefined) return <span className="text-muted">—</span>
  return (
    <pre className="max-h-72 overflow-auto rounded-lg border border-line bg-page p-3 font-mono text-[12px] leading-relaxed text-fg-2">
      {JSON.stringify(value, null, 2)}
    </pre>
  )
}

export function KeyValue({ items }: { items: [ReactNode, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-[minmax(7.5rem,auto)_1fr] text-[13.5px]">
      {items.map(([k, v], i) => (
        <div key={i} className="contents">
          <dt className="border-b border-line py-2.5 pr-4 text-muted">{k}</dt>
          <dd className="min-w-0 border-b border-line py-2.5 break-words text-fg">{v}</dd>
        </div>
      ))}
    </dl>
  )
}

// The keyboard and screen-reader entry to a clickable table row: the row's
// main label as a real button. The <tr> keeps its onClick for mouse users.
export function RowButton({ onClick, children }: { onClick: () => void; children: ReactNode }) {
  return (
    <button type="button" onClick={(e) => { e.stopPropagation(); onClick() }}
            className="-mx-1 inline-flex min-h-6 items-center rounded px-1 text-left font-medium text-fg hover:underline">
      {children}
    </button>
  )
}

// Link-like action used in card headers and rows ("View all →"). At least
// 24px tall so it meets the minimum target size (WCAG 2.5.8).
export function More({ to, children }: { to: string; children: ReactNode }) {
  return (
    <Link to={to} className="group inline-flex min-h-6 items-center gap-1 text-[12.5px] font-medium text-muted hover:text-fg">
      {children}
      <ArrowRightIcon size={12} className="transition-transform group-hover:translate-x-0.5" />
    </Link>
  )
}
