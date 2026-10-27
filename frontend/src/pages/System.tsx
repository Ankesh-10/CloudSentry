import { useState } from 'react'

import { ConfirmDialog } from '../components/Dialog'
import { PendingChanges } from '../components/PendingChanges'
import { toast } from '../components/Toast'
import { Badge, Button, Card, Dot, Empty, ErrorBox, Grid, KeyValue, PageHeader, Skeleton, StatusBadge, Table, Tip } from '../components/ui'
import { useSystemConfig, useSystemHealth, useUpdateConfig } from '../hooks/queries'
import { dateTime, humanize, relTime } from '../lib/format'
import { READ_ONLY_HINT, useCanOperate } from '../lib/role'

const CONFIG_HELP: Record<string, string> = {
  GLOBAL_AUTOMATION_ENABLED: 'Kill-switch. Turning it on needs a second decision.',
  DRY_RUN_MODE: 'Log “would act” only. Turning it off needs a second decision.',
  MAX_ACTIONS_PER_DAY: 'State-changing actions per day',
  ACTION_COOLDOWN_MINUTES: 'Minimum gap between actions on one resource',
  MAX_CW_API_CALLS_PER_HOUR: 'CloudWatch API budget',
  MAX_DAILY_SPEND_USD: 'Budget gate for spend-raising actions',
  MAX_MONTHLY_BUDGET_USD: 'Budget gate for spend-raising actions',
}

// Turning automation on or dry-run off is the unsafe direction (two-person rule).
const isUnsafe = (key: string, next: string) =>
  (key === 'GLOBAL_AUTOMATION_ENABLED' && next === 'true') || (key === 'DRY_RUN_MODE' && next === 'false')

function ConfigTable() {
  const { data, error, isLoading } = useSystemConfig()
  const update = useUpdateConfig()
  const canOperate = useCanOperate()
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [change, setChange] = useState<{ key: string; from: string; to: string } | null>(null)
  const mode = useSystemHealth().data?.automation.two_person

  function submit() {
    if (!change) return
    update.mutate(
      { key: change.key, value: change.to },
      {
        onSuccess: (r) => {
          setChange(null)
          setDrafts((d) => ({ ...d, [change.key]: '' }))
          if (r.status === 'pending_confirmation') {
            toast.info('Change requested', r.confirmable_at
              ? `You can confirm it yourself from ${dateTime(r.confirmable_at)}, under Pending changes.`
              : `Another operator can confirm it until ${dateTime(r.expires_at)}.`)
          } else toast.success(`${r.key} updated`, `Now ${String(r.value)}`)
        },
        onError: (e) => { setChange(null); toast.error(`Could not change ${change.key}`, e.message) },
      },
    )
  }

  return (
    <Card title="Runtime configuration" subtitle="Changes are audited · loosening one needs a second decision" className="lg:col-span-8" flush>
      {error ? <div className="px-6 pb-6"><ErrorBox error={error} /></div> : isLoading || !data ? <div className="px-6 pb-6"><Skeleton className="h-40" /></div> : (
        <Table head={<><th className="th">Key</th><th className="th">Value</th><th className="th text-right">Change</th></>}>
          {Object.entries(data).sort(([a], [b]) => a.localeCompare(b)).map(([k, v]) => {
            const isBool = typeof v === 'boolean'
            const draft = drafts[k] ?? ''
            return (
              <tr key={k}>
                <td className="td">
                  <code className="font-mono text-[12.5px] font-medium text-fg">{k}</code>
                  <div className="text-[12.5px] text-muted">{CONFIG_HELP[k] ?? ''}</div>
                </td>
                <td className="td">
                  {isBool ? (
                    <Badge tone={(k === 'GLOBAL_AUTOMATION_ENABLED' && v) || (k === 'DRY_RUN_MODE' && !v) ? 'critical' : k === 'DRY_RUN_MODE' ? 'rehearsal' : v ? 'good' : 'neutral'}>
                      {v ? 'On' : 'Off'}
                    </Badge>
                  ) : (
                    <span className="text-[14px] font-semibold text-fg tabular-nums">{v}</span>
                  )}
                </td>
                <td className="td text-right">
                  <Tip content={canOperate ? null : READ_ONLY_HINT}>
                    <span className="inline-flex justify-end gap-1.5">
                      {isBool ? (
                        <Button size="sm" variant={isUnsafe(k, String(!v)) ? 'danger-outline' : 'secondary'} disabled={!canOperate}
                                onClick={() => setChange({ key: k, from: String(v), to: String(!v) })}>
                          Turn {v ? 'off' : 'on'}
                        </Button>
                      ) : (
                        <form className="flex gap-1.5" onSubmit={(e) => { e.preventDefault(); if (draft && draft !== String(v)) setChange({ key: k, from: String(v), to: draft }) }}>
                          <input className="input h-7 w-24" inputMode="decimal" placeholder={String(v)} value={draft} disabled={!canOperate}
                                 onChange={(e) => setDrafts({ ...drafts, [k]: e.target.value })} aria-label={`New value for ${k}`} />
                          <Button size="sm" type="submit" disabled={!canOperate || !draft || draft === String(v)}>Set</Button>
                        </form>
                      )}
                    </span>
                  </Tip>
                </td>
              </tr>
            )
          })}
        </Table>
      )}
      <ConfirmDialog
        open={!!change}
        onOpenChange={(o) => !o && setChange(null)}
        title={change ? `Set ${change.key}` : ''}
        description={change ? `${change.from} → ${change.to}` : undefined}
        tone={change && isUnsafe(change.key, change.to) ? 'live' : 'confirm'}
        consequence={
          change && isUnsafe(change.key, change.to)
            ? mode === 'solo_delay'
              ? 'This loosens a safety limit, so it needs a second decision: confirm it yourself after 15 minutes under Pending changes, or another operator can confirm it now. The request expires in an hour.'
              : 'This loosens a safety limit. It stays pending until a different operator makes the same change within an hour.'
            : 'Takes effect immediately on every replica and is written to the audit log.'
        }
        confirmLabel={change && isUnsafe(change.key, change.to) ? 'Request change' : 'Apply'}
        pending={update.isPending}
        onConfirm={submit}
      />
    </Card>
  )
}

export default function System() {
  const { data, error, isLoading } = useSystemHealth()

  return (
    <>
      <PageHeader title="System" kicker="Control" subtitle="Scheduler, jobs, ML models and the safety configuration." />
      {error && <div className="mb-4"><ErrorBox error={error} /></div>}
      {isLoading || !data ? (!error && <Skeleton className="h-64" />) : (
        <Grid className="lg:grid-cols-12">
          <PendingChanges className="lg:col-span-12" />
          <Card title="Status" className="lg:col-span-4">
            <div className={`figure text-[2.25rem] ${data.status === 'unhealthy' ? 'text-accent' : 'text-fg'}`}>
              {data.status === 'ok' ? 'Healthy' : data.status === 'degraded' ? 'Degraded' : 'Down'}
            </div>
            <div className="mt-4">
              <KeyValue
                items={[
                  ['Database', <span>{data.db.status}{data.db.latency_ms !== undefined && <span className="text-muted"> · {data.db.latency_ms} ms</span>}</span>],
                  ['Scheduler', `${data.scheduler.running ? 'Running' : 'Stopped'} · ${data.scheduler.leader ? 'leader' : 'follower'}`],
                  ['Telemetry', data.telemetry.last_collection ? `${relTime(data.telemetry.last_collection)} (${data.telemetry.staleness_minutes} min)` : 'Never'],
                  ['Automation', `${data.automation.enabled ? 'On' : 'Off'} · ${data.automation.dry_run ? 'dry run' : 'LIVE'}`],
                  ['Budget', data.budget ? (data.budget.over_budget ? <span className="text-accent">Over — {data.budget.reason}</span> : 'Within limits') : '—'],
                ]}
              />
            </div>
            {data.issues.length > 0 && (
              <div className="mt-4 flex flex-wrap gap-1.5">
                {data.issues.map((i) => <Badge key={i} tone="critical">{i}</Badge>)}
              </div>
            )}
          </Card>

          <Card title="Scheduled jobs" subtitle="Only the leader replica runs jobs" className="lg:col-span-8" flush>
            {!Object.keys(data.jobs).length ? <Empty title="No runs yet">No job runs recorded yet.</Empty> : (
              <Table head={<><th className="th">Job</th><th className="th">Last success</th><th className="th">Last failure</th><th className="th">Streak</th><th className="th">Last error</th></>}>
                {Object.entries(data.jobs).sort(([a], [b]) => a.localeCompare(b)).map(([name, j]) => {
                  const f = j.consecutive_failures ?? 0
                  return (
                    <tr key={name} className="hover:bg-subtle">
                      <td className="td">
                        <span className="inline-flex items-center gap-2 font-medium text-fg">
                          <Dot tone={f >= 3 ? 'critical' : f ? 'serious' : j.last_success ? 'good' : 'neutral'} />
                          {name}
                        </span>
                      </td>
                      <td className="td text-fg-2">{relTime(j.last_success)}</td>
                      <td className="td text-fg-2">{relTime(j.last_failure)}</td>
                      <td className="td">
                        <span className="inline-flex gap-[3px]" aria-label={`${f} consecutive failures`}>
                          {Array.from({ length: 5 }, (_, i) => (
                            <span key={i} className={`inline-block h-3 w-1.5 rounded-full ${i < f ? 'bg-accent' : 'bg-line'}`} />
                          ))}
                        </span>
                      </td>
                      <td className="td max-w-[16rem] truncate text-[12.5px] text-muted" title={j.last_error ?? ''}>{j.last_error ?? '—'}</td>
                    </tr>
                  )
                })}
              </Table>
            )}
          </Card>

          <Card title="ML models" subtitle={humanize(data.ml_model.mode)} className="lg:col-span-4">
            {!data.ml_model.details || !Object.keys(data.ml_model.details).length ? (
              <Empty title="Cold start">No trained models yet. Detection uses z-score / EWMA until a model is promoted.</Empty>
            ) : (
              <div className="divide-y divide-line">
                {Object.entries(data.ml_model.details).map(([type, meta]) => (
                  <div key={type} className="py-3">
                    <div className="flex items-baseline justify-between">
                      <span className="text-[15px] font-semibold text-fg uppercase">{type}</span>
                      <StatusBadge value="ok" />
                    </div>
                    <div className="mt-2 grid grid-cols-2 gap-y-0.5 text-[12.5px] text-muted">
                      <span>Version</span><span className="text-fg-2">{String(meta.version ?? '—')}</span>
                      <span>Trained</span><span className="text-fg-2">{relTime(meta.trained_at as string | undefined)}</span>
                      {meta.n_samples !== undefined && <><span>Samples</span><span className="text-fg-2">{Number(meta.n_samples).toLocaleString()}</span></>}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </Card>

          <ConfigTable />
        </Grid>
      )}
    </>
  )
}
