import { useMemo, useState, type ReactNode } from 'react'
import { Link, useNavigate } from 'react-router-dom'

import { BarList, Sparkline, StackedBar, dailyBuckets } from '../components/charts'
import { ActionWhy } from '../components/ActionWhy'
import { ConfirmDialog } from '../components/Dialog'
import { describePending, stageOf, useIsMine } from '../components/PendingChanges'
import { AnomalyTimelinePanel, SavingsPanel, SpendPanel, useSeverityKeys } from '../components/insights'
import { toast } from '../components/Toast'
import {
  Badge,
  Button,
  Card,
  Dot,
  Empty,
  ErrorBox,
  Grid,
  More,
  PageHeader,
  Panel,
  Segmented,
  Skeleton,
  Stat,
  Tip,
} from '../components/ui'
import {
  useActionOp,
  useActions,
  useAnomalies,
  useAnomalySummary,
  useDashboardSummary,
  usePendingChanges,
  useResources,
  useSystemHealth,
} from '../hooks/queries'
import { effectOf } from '../lib/consequence'
import { humanize, relTime, usd } from '../lib/format'
import { READ_ONLY_HINT, useCanOperate } from '../lib/role'
import type { Action } from '../lib/types'

type Range = 7 | 30 | 90
const RANGES: { value: Range; label: string }[] = [
  { value: 7, label: '7 days' },
  { value: 30, label: '30 days' },
  { value: 90, label: '90 days' },
]

/* ---------- agent state ------------------------------------------------------------- */

// Loosening requests waiting for their second decision, e.g. "Turn dry-run off
// is pending · you can confirm at 14:32". Links to the System page list.
function PendingLine() {
  const { data } = usePendingChanges()
  const isMine = useIsMine()
  const live = (data ?? []).filter((p) => !p.expired && Date.parse(p.expires_at) > Date.now())
  if (!live.length) return null
  const p = live[0]
  const stage = stageOf(p, isMine(p), Date.now())
  const when = stage.kind === 'waiting'
    ? `you can confirm at ${new Date(stage.until).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`
    : stage.kind === 'others' ? 'waiting for another operator' : 'ready to confirm'
  return (
    <p className="text-[13px] text-fg-2">
      <Dot tone="warning" /> <Link to="/system" className="text-info hover:underline">{describePending(p)}</Link> is pending · {when}
      {live.length > 1 && <> · {live.length - 1} more</>}
    </p>
  )
}

// First thing on the page: is the agent armed, does anything need me, is it watching.
function AgentState({ index }: { index: number }) {
  const summary = useDashboardSummary()
  const health = useSystemHealth()
  const pending = useActions({ status: 'pending_approval', limit: 100 })
  const s = summary.data
  const h = health.data
  const waiting = pending.data?.length

  const mode = !s
    ? null
    : !s.automation_enabled
      ? { tone: 'neutral' as const, word: 'Off', line: 'Nothing runs automatically. Approved actions are blocked by the kill-switch.' }
      : s.dry_run
        ? { tone: 'rehearsal' as const, word: 'On · dry run', line: 'Actions run as rehearsals: logged as “would act”, nothing changes in the cloud.' }
        : { tone: 'critical' as const, word: 'On · live', line: 'Approved actions change real cloud resources after the safety checks.' }

  const cell = 'flex min-w-0 flex-col gap-1.5 px-5 py-4 sm:px-6 sm:py-5'
  return (
    <Panel className="grid grid-cols-2 md:grid-cols-[minmax(0,1.7fr)_minmax(0,1fr)_minmax(0,1fr)] lg:col-span-12" index={index}>
      <div className={`${cell} col-span-2 border-b border-line md:col-span-1 md:border-r md:border-b-0`}>
        <h2 className="label text-muted">Automation</h2>
        {!mode ? <Skeleton className="h-12" /> : (
          <>
            <div className="flex items-center gap-2.5">
              <Dot tone={mode.tone} />
              <span className={`figure text-[1.6rem] ${mode.tone === 'critical' ? 'text-accent' : 'text-fg'}`}>{mode.word}</span>
            </div>
            <p className="text-[13px] text-fg-2">
              {mode.line} <Link to="/system" className="whitespace-nowrap text-info hover:underline">Change on System</Link>
            </p>
            <PendingLine />
          </>
        )}
      </div>
      <div className={`${cell} border-r border-line`}>
        <h2 className="label text-muted">Waiting for you</h2>
        {waiting === undefined ? <Skeleton className="h-12" /> : (
          <>
            <span className="figure text-[1.6rem] text-fg tabular-nums">{waiting}</span>
            <p className="text-[13px] text-fg-2">
              {waiting ? <Link to="/actions?status=pending_approval" className="text-info hover:underline">{waiting === 1 ? 'Proposal' : 'Proposals'} to review</Link> : 'No proposals to review'}
            </p>
          </>
        )}
      </div>
      <div className={cell}>
        <h2 className="label text-muted">Watching</h2>
        {!h ? <Skeleton className="h-12" /> : (
          <>
            <div className="flex items-center gap-2.5">
              <Dot tone={h.status === 'ok' ? 'good' : h.status === 'degraded' ? 'warning' : 'critical'} />
              <span className="figure text-[1.6rem] text-fg">{h.status === 'ok' ? 'Healthy' : h.status === 'degraded' ? 'Degraded' : 'Down'}</span>
            </div>
            <p className="text-[13px] text-fg-2">
              Telemetry {h.telemetry.last_collection ? relTime(h.telemetry.last_collection) : 'never collected'}
              {h.issues.length > 0 && <> · <Link to="/system" className="whitespace-nowrap text-info hover:underline">{h.issues.length} issue{h.issues.length === 1 ? '' : 's'}</Link></>}
            </p>
          </>
        )}
      </div>
    </Panel>
  )
}

/* ---------- headline figures ------------------------------------------------------- */

function Figures({ index }: { index: number }) {
  const summary = useDashboardSummary()
  const sev = useAnomalySummary()
  const recent = useAnomalies({ limit: 500 })
  const open = useActions({ status: 'pending_approval', limit: 100 })
  const s = summary.data
  const series = useMemo(() => dailyBuckets(recent.data ?? [], (a) => a.detected_at, 14).map((b) => b.value), [recent.data])

  return (
    <Panel className="grid divide-y divide-line lg:col-span-12 lg:grid-cols-3 lg:divide-x lg:divide-y-0 xl:col-span-4 xl:grid-cols-1 xl:divide-x-0 xl:divide-y" index={index}>
      {!s ? (
        [0, 1, 2].map((i) => <div key={i} className="p-6"><Skeleton className="h-20" /></div>)
      ) : (
        <>
          <Stat
            label="Active anomalies"
            value={s.active_anomalies}
            alert={(sev.data?.HIGH ?? 0) > 0}
            trend={<Sparkline values={series} label="Anomalies detected per day, last 14 days" />}
            hint={sev.data ? `${sev.data.HIGH} high severity · daily trend, 14 days` : undefined}
          />
          <Stat
            label="Potential savings"
            value={usd(s.potential_savings_usd)}
            hint={open.data ? `${open.data.length} proposal${open.data.length === 1 ? '' : 's'} awaiting approval` : 'From open proposals'}
          />
          <Stat label="Realized savings" value={usd(s.realized_savings_usd)} hint={`${s.active_resources} resources monitored · monthly est.`} />
        </>
      )}
    </Panel>
  )
}

/* ---------- needs attention ---------------------------------------------------------- */

function AttentionRow({ title, meta, badge, children }: { title: ReactNode; meta: ReactNode; badge: ReactNode; children?: ReactNode }) {
  return (
    <li className="flex items-center gap-4 px-6 py-3.5 transition-colors hover:bg-subtle">
      <div className="w-24 shrink-0 max-sm:hidden">{badge}</div>
      <div className="min-w-0 flex-1">
        <div className="mb-1 sm:hidden">{badge}</div>
        <div className="truncate text-[13.5px] font-medium text-fg">{title}</div>
        <div className="mt-0.5 truncate text-[12.5px] text-muted">{meta}</div>
      </div>
      <div className="flex shrink-0 items-center gap-3">{children}</div>
    </li>
  )
}

function Attention({ index }: { index: number }) {
  const pending = useActions({ status: 'pending_approval', limit: 100 })
  const active = useAnomalies({ status: 'active', limit: 200 })
  const summary = useDashboardSummary()
  const op = useActionOp()
  const canOperate = useCanOperate()
  const [confirm, setConfirm] = useState<Action | null>(null)
  const effect = confirm ? effectOf('approve', confirm, summary.data) : null

  const high = (active.data ?? []).filter((a) => a.severity === 'HIGH').slice(0, 4)
  const approvals = (pending.data ?? []).slice(0, 5)
  const loading = pending.isLoading || active.isLoading

  return (
    <Card title="Needs your attention" subtitle="Approvals waiting and high-severity anomalies" className="lg:col-span-12 xl:col-span-8" flush index={index}
          actions={<More to="/actions">All actions</More>}>
      {loading ? (
        <div className="space-y-3 px-6 pb-6">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-10" />)}</div>
      ) : !approvals.length && !high.length ? (
        <Empty title="All clear">No approvals waiting and no high-severity anomalies.</Empty>
      ) : (
        <ul className="divide-y divide-line border-t border-line">
          {approvals.map((a) => (
            <AttentionRow
              key={a.id}
              badge={<Badge tone="warning">Awaiting</Badge>}
              title={<>{humanize(a.action_type)}{!!a.estimated_savings_usd && <span className="font-normal text-muted"> · saves {usd(a.estimated_savings_usd)}/mo</span>}</>}
              meta={<>{a.risk_level ?? 'Unknown'} risk · {a.dry_run ? 'dry run' : 'live change'} · proposed {relTime(a.created_at)}</>}
            >
              {a.resource_id && (
                <Link to={`/resources/${a.resource_id}`} className="hidden min-h-6 items-center text-[12.5px] text-muted hover:text-fg sm:inline-flex">Resource</Link>
              )}
              <Tip content={canOperate ? null : READ_ONLY_HINT}>
                <span>
                  <Button size="sm" variant="primary" disabled={!canOperate} onClick={() => setConfirm(a)}>Approve</Button>
                </span>
              </Tip>
            </AttentionRow>
          ))}
          {high.map((a) => (
            <AttentionRow
              key={a.id}
              badge={<Badge tone="critical">High</Badge>}
              title={humanize(a.anomaly_type)}
              meta={<>{a.reason ?? 'High severity'} · {relTime(a.detected_at)}</>}
            >
              {a.resource_id && <More to={`/resources/${a.resource_id}`}>Investigate</More>}
            </AttentionRow>
          ))}
        </ul>
      )}
      <ConfirmDialog
        open={!!confirm}
        onOpenChange={(o) => !o && setConfirm(null)}
        title={`Approve ${humanize(confirm?.action_type)}`}
        description="Every safety check (kill-switch, protection tags, caps, cooldown, budget) runs before anything changes."
        tone={effect?.live ? 'live' : 'confirm'}
        consequence={effect?.text}
        confirmLabel="Approve"
        pending={op.isPending}
        onConfirm={() =>
          confirm &&
          op.mutate(
            { id: confirm.id, op: 'approve' },
            {
              onSuccess: () => { setConfirm(null); toast.success('Action approved', 'Queued for execution.') },
              onError: (e) => { setConfirm(null); toast.error('Could not approve', e.message) },
            },
          )
        }
      >
        {confirm && <ActionWhy action={confirm} />}
      </ConfirmDialog>
    </Card>
  )
}

/* ---------- anomaly breakdown -------------------------------------------------------- */

function AnomalyBreakdown({ index }: { index: number }) {
  const sev = useAnomalySummary()
  const keys = useSeverityKeys()
  const active = useAnomalies({ status: 'active', limit: 200 })
  const navigate = useNavigate()
  const byType = useMemo(() => {
    const m = new Map<string, number>()
    for (const a of active.data ?? []) m.set(a.anomaly_type ?? 'unknown', (m.get(a.anomaly_type ?? 'unknown') ?? 0) + 1)
    return [...m.entries()].sort((a, b) => b[1] - a[1]).slice(0, 5)
  }, [active.data])
  const color = Object.fromEntries(keys.map((k) => [k.key, k.color]))

  return (
    <Card title="Open anomalies" subtitle="By severity, then by type" className="lg:col-span-6 xl:col-span-4" index={index}
          actions={<More to="/anomalies">View</More>}>
      {sev.error ? <ErrorBox error={sev.error} /> : !sev.data ? <Skeleton className="h-16" /> : (
        <StackedBar
          emptyLabel="No active anomalies"
          segments={[
            { key: 'HIGH', label: 'High', value: sev.data.HIGH, color: color.HIGH },
            { key: 'MEDIUM', label: 'Medium', value: sev.data.MEDIUM, color: color.MEDIUM },
            { key: 'LOW', label: 'Low', value: sev.data.LOW, color: color.LOW },
          ]}
        />
      )}
      {byType.length > 0 && (
        <div className="-mx-2 mt-5 border-t border-line pt-3">
          <BarList rows={byType.map(([k, v]) => ({ key: k, label: humanize(k), value: v }))} onSelect={() => navigate('/anomalies')} />
        </div>
      )}
    </Card>
  )
}

/* ---------- action pipeline ---------------------------------------------------------- */

function Pipeline({ index }: { index: number }) {
  const { data, isLoading } = useActions({ limit: 500 })
  const c = useMemo(() => {
    const n: Record<string, number> = {}
    for (const a of data ?? []) n[a.status ?? 'unknown'] = (n[a.status ?? 'unknown'] ?? 0) + 1
    return n
  }, [data])
  const stages = [
    { key: 'proposed', label: 'Proposed', value: (c.pending ?? 0) + (c.pending_approval ?? 0) },
    { key: 'approved', label: 'Approved', value: c.approved ?? 0 },
    { key: 'executing', label: 'Running', value: c.executing ?? 0 },
    { key: 'completed', label: 'Done', value: c.completed ?? 0 },
  ]
  const max = Math.max(1, ...stages.map((s) => s.value))
  const side = [
    { label: 'Failed', value: c.failed ?? 0, tone: 'critical' as const },
    { label: 'Rejected', value: c.rejected ?? 0, tone: 'neutral' as const },
    { label: 'Rolled back', value: c.rolled_back ?? 0, tone: 'neutral' as const },
  ]
  return (
    <Card title="Action pipeline" subtitle={data && data.length >= 500 ? 'Latest 500 actions' : 'Proposals by stage'} className="lg:col-span-6 xl:col-span-4" index={index}>
      {isLoading ? <Skeleton className="h-32" /> : (
        <>
          <div className="grid grid-cols-4 gap-3" role="img" aria-label={stages.map((s) => `${s.label} ${s.value}`).join(', ')}>
            {stages.map((s) => (
              <Tip key={s.key} content={`${s.value} ${s.label.toLowerCase()}`}>
                <div className="flex flex-col">
                  <div className="figure text-[1.6rem] text-fg">{s.value}</div>
                  <div className="mt-3 flex h-14 items-end border-b border-[var(--chart-axis)]">
                    <div className="w-full max-w-[24px] rounded-t-[4px] bg-series-1" style={{ height: `${Math.max((s.value / max) * 100, s.value ? 6 : 0)}%` }} />
                  </div>
                  <div className="mt-2 truncate text-[12px] text-muted">{s.label}</div>
                </div>
              </Tip>
            ))}
          </div>
          <div className="mt-5 flex flex-wrap gap-2 border-t border-line pt-4">
            {side.map((s) => (
              <Badge key={s.label} tone={s.value ? s.tone : 'neutral'}>
                {s.label} {s.value}
              </Badge>
            ))}
          </div>
        </>
      )}
    </Card>
  )
}

/* ---------- inventory ---------------------------------------------------------------- */

function Inventory({ index }: { index: number }) {
  const { data, isLoading, error } = useResources({})
  const navigate = useNavigate()
  const rows = useMemo(() => {
    const m = new Map<string, number>()
    for (const r of data ?? []) m.set(r.resource_type, (m.get(r.resource_type) ?? 0) + 1)
    return [...m.entries()].sort((a, b) => b[1] - a[1])
  }, [data])
  return (
    <Card title="Inventory" subtitle={data ? `${data.length} resources by type` : 'Resources by type'} className="lg:col-span-4" index={index}>
      {error ? <ErrorBox error={error} /> : isLoading ? <Skeleton className="h-32" /> : !rows.length ? (
        <Empty title="Nothing discovered yet">Run discovery from the Resources page.</Empty>
      ) : (
        <div className="-mx-2">
          <BarList
            rows={rows.map(([k, v]) => ({ key: k, label: k.toUpperCase(), value: v, hint: `Show ${k} resources` }))}
            onSelect={(k) => navigate(`/resources?type=${k}`)}
          />
        </div>
      )}
    </Card>
  )
}

/* ---------- health ------------------------------------------------------------------- */

function Health({ index }: { index: number }) {
  const { data, error } = useSystemHealth()
  return (
    <Card title="System health" className="lg:col-span-12 xl:col-span-4" index={index} actions={<More to="/system">Details</More>}>
      {error ? <ErrorBox error={error} /> : !data ? <Skeleton className="h-32" /> : (
        <div>
          <div className="flex items-center gap-3">
            <div className={`figure text-[1.6rem] ${data.status === 'unhealthy' ? 'text-accent' : 'text-fg'}`}>
              {data.status === 'ok' ? 'All systems normal' : data.status === 'degraded' ? 'Degraded' : 'Database down'}
            </div>
          </div>
          <div className="mt-2 text-[12.5px] text-muted">
            Telemetry {data.telemetry.last_collection ? relTime(data.telemetry.last_collection) : 'never'} · DB {data.db.latency_ms ?? '—'} ms
            {data.issues.length > 0 && ` · ${data.issues.length} issue${data.issues.length === 1 ? '' : 's'}`}
          </div>
          <div className="mt-4 grid grid-cols-2 gap-x-4 border-t border-line">
            {Object.entries(data.jobs).sort(([a], [b]) => a.localeCompare(b)).map(([name, j]) => {
              const failing = (j.consecutive_failures ?? 0) > 0
              return (
                <Tip key={name} content={failing ? `${j.consecutive_failures} failure(s) in a row${j.last_error ? ` — ${j.last_error}` : ''}` : `Last success ${relTime(j.last_success)}`}>
                  <div className="flex items-center gap-2 border-b border-line py-2 text-[12.5px]">
                    <Dot tone={failing ? ((j.consecutive_failures ?? 0) >= 3 ? 'critical' : 'warning') : j.last_success ? 'good' : 'neutral'} />
                    <span className={`truncate ${failing ? 'text-accent' : 'text-fg-2'}`}>{name}</span>
                    <span className="ml-auto shrink-0 text-muted">{relTime(j.last_success)}</span>
                  </div>
                </Tip>
              )
            })}
          </div>
          {data.issues.length > 0 && (
            <div className="mt-3 flex flex-wrap gap-1.5">
              {data.issues.map((i) => <Badge key={i} tone="critical">{i}</Badge>)}
            </div>
          )}
        </div>
      )}
    </Card>
  )
}

/* ---------- page ------------------------------------------------------------------------ */

export default function Dashboard() {
  const [range, setRange] = useState<Range>(30)
  const summary = useDashboardSummary()

  return (
    <>
      <PageHeader
        title="Overview"
        kicker="Cloud cost intelligence"
        subtitle="What the agent is doing, what needs you, then spend, savings and anomalies across your cloud accounts."
      />
      {summary.error && <div className="mb-4"><ErrorBox error={summary.error} /></div>}

      {/* Now: state, then decisions, then what the agent is working through. */}
      <Grid className="lg:grid-cols-12">
        <AgentState index={0} />
        <Attention index={1} />
        <Figures index={2} />
        <Pipeline index={0} />
        <AnomalyBreakdown index={1} />
        <Health index={2} />
      </Grid>

      {/* Trends: the only part the time range applies to. */}
      <section aria-labelledby="trends-heading" className="mt-14">
        <div className="mb-5 flex flex-wrap items-end justify-between gap-x-8 gap-y-3">
          <h2 id="trends-heading" className="display text-[1.65rem] text-fg">Trends</h2>
          <Segmented label="Time range" value={range} onChange={setRange} options={RANGES} />
        </div>
        <Grid className="lg:grid-cols-12">
          <SpendPanel range={range} className="lg:col-span-8" index={0} />
          <Inventory index={1} />
          <SavingsPanel range={range} className="lg:col-span-6" index={0} />
          <AnomalyTimelinePanel range={range} className="lg:col-span-6" index={1} />
        </Grid>
      </section>
    </>
  )
}
