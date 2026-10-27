import { useMemo } from 'react'
import { Link } from 'react-router-dom'

import { BarList, Columns } from '../components/charts'
import { Drawer } from '../components/Dialog'
import { AnomalyTimelinePanel } from '../components/insights'
import { toast } from '../components/Toast'
import { Button, Card, Empty, ErrorBox, Grid, Json, KeyValue, PageHeader, Panel, RowButton, Segmented, Skeleton, StatusBadge, Table, Tip } from '../components/ui'
import { useAnomalies, useResources, useSetAnomalyStatus } from '../hooks/queries'
import { useUrlState } from '../hooks/useUrlState'
import { dateTime, humanize, num, relTime } from '../lib/format'
import { READ_ONLY_HINT, useCanOperate } from '../lib/role'
import type { Anomaly, AnomalyStatus } from '../lib/types'

// The reason box takes its severity's tint, so a LOW anomaly doesn't read as an alarm.
const REASON_BG: Record<string, string> = { HIGH: 'bg-accent-soft', MEDIUM: 'bg-warn-soft', LOW: 'bg-subtle' }

const STATUSES: { value: AnomalyStatus | 'all'; label: string }[] = [
  { value: 'active', label: 'Active' },
  { value: 'resolved', label: 'Resolved' },
  { value: 'false_positive', label: 'False positive' },
  { value: 'all', label: 'All' },
]

function AnomalyDrawer({ a, resourceName, onClose }: { a: Anomaly | null; resourceName?: string; onClose: () => void }) {
  const set = useSetAnomalyStatus()
  const canOperate = useCanOperate()
  if (!a) return null

  function change(status: AnomalyStatus, verb: string) {
    set.mutate(
      { id: a!.id, status },
      {
        onSuccess: () => { toast.success(`Anomaly ${verb}`); onClose() },
        onError: (e) => toast.error(`Could not mark ${verb}`, e.message),
      },
    )
  }

  return (
    <Drawer open onOpenChange={(o) => !o && onClose()} eyebrow={`Anomaly ${a.id.slice(0, 8)}`} title={humanize(a.anomaly_type)}
            subtitle={`${a.severity ?? '—'} severity · detected ${relTime(a.detected_at)}`}>
      <div className={`mb-5 rounded-lg px-4 py-3 text-[13.5px] text-fg-2 ${REASON_BG[a.severity ?? ""] ?? "bg-subtle"}`}>{a.reason ?? 'No reason recorded.'}</div>
      <KeyValue
        items={[
          ['Status', <StatusBadge value={a.status} />],
          ['Severity', <StatusBadge value={a.severity} />],
          ['Resource', a.resource_id ? <Link className="font-medium text-info hover:underline" to={`/resources/${a.resource_id}`}>{resourceName ?? a.resource_id.slice(0, 8)}</Link> : '—'],
          ['Score', num(a.anomaly_score)],
          ['Confidence', a.confidence != null ? `${Math.round(a.confidence * 100)}%` : '—'],
          ['Model', a.model_version ? <code className="font-mono text-[12.5px]">{a.model_version}</code> : '—'],
          ['Detected', dateTime(a.detected_at)],
          ['Resolved', dateTime(a.resolved_at)],
        ]}
      />
      <div className="eyebrow mt-6 mb-2 text-muted">Feature snapshot</div>
      <Json value={a.features_snapshot} />
      <div className="mt-6 flex flex-wrap gap-2 border-t border-line pt-5">
        <Tip content={canOperate ? null : READ_ONLY_HINT}>
          <span className="flex gap-2">
            {a.status === 'active' ? (
              <>
                <Button variant="primary" disabled={!canOperate || set.isPending} onClick={() => change('resolved', 'resolved')}>Mark resolved</Button>
                <Button disabled={!canOperate || set.isPending} onClick={() => change('false_positive', 'false positive')}>False positive</Button>
              </>
            ) : (
              <Button disabled={!canOperate || set.isPending} onClick={() => change('active', 'reopened')}>Reopen</Button>
            )}
          </span>
        </Tip>
      </div>
      <p className="mt-3 text-[12.5px] text-muted">Status changes need the operator role and are written to the audit log.</p>
    </Drawer>
  )
}

// Equal-width bins across the observed score range (scores are model-specific).
function scoreBins(values: number[], n = 10) {
  if (!values.length) return []
  const lo = Math.min(...values)
  const hi = Math.max(...values)
  const w = (hi - lo) / n || 1
  const out = Array.from({ length: n }, (_, i) => ({ key: `${(lo + i * w).toFixed(2)}–${(lo + (i + 1) * w).toFixed(2)}`, value: 0 }))
  for (const v of values) out[Math.min(n - 1, Math.floor((v - lo) / w))].value += 1
  return out
}

function tally(items: Anomaly[], key: (a: Anomaly) => string | null | undefined, limit = 6) {
  const m = new Map<string, number>()
  for (const a of items) {
    const k = key(a) ?? 'unknown'
    m.set(k, (m.get(k) ?? 0) + 1)
  }
  return [...m.entries()].sort((a, b) => b[1] - a[1]).slice(0, limit)
}

export default function Anomalies() {
  const [status, setStatus] = useUrlState('status', 'active')
  const [sev, setSev] = useUrlState('severity')
  const [openId, setOpenId] = useUrlState('id')
  const all = useAnomalies({ limit: 500 })
  const list = useAnomalies({ status: status === 'all' ? undefined : (status as AnomalyStatus), limit: 500 })
  const resources = useResources({ include_deleted: true })

  const names = useMemo(() => new Map((resources.data ?? []).map((r) => [r.id, r.name || r.provider_id])), [resources.data])
  const counts = useMemo(() => {
    const c: Record<string, number> = { all: all.data?.length ?? 0 }
    for (const a of all.data ?? []) c[a.status ?? 'active'] = (c[a.status ?? 'active'] ?? 0) + 1
    return c
  }, [all.data])
  const insight = useMemo(() => {
    const d = all.data ?? []
    const conf = d.map((a) => a.confidence).filter((v): v is number => v != null)
    return {
      bins: scoreBins(d.map((a) => a.anomaly_score).filter((v): v is number => v != null)),
      byType: tally(d, (a) => a.anomaly_type),
      byModel: tally(d, (a) => a.model_version),
      meanConfidence: conf.length ? Math.round((conf.reduce((s, v) => s + v, 0) / conf.length) * 100) : null,
    }
  }, [all.data])
  const rows = useMemo(() => (list.data ?? []).filter((a) => !sev || a.severity === sev), [list.data, sev])
  const open = rows.find((a) => a.id === openId) ?? (list.data ?? []).find((a) => a.id === openId) ?? null
  const sample = all.data && all.data.length >= 500 ? 'Latest 500 detections' : `${all.data?.length ?? 0} detections`

  return (
    <>
      <PageHeader
        title="Anomalies"
        kicker="Detection"
        subtitle="Rule, z-score/EWMA and Isolation Forest detections. Select a row for details; changing status needs the operator role."
      />

      <Grid className="mb-5 lg:grid-cols-12">
        <AnomalyTimelinePanel range={30} className="lg:col-span-8" index={0} />
        <Panel className="flex flex-col lg:col-span-4" index={1}>
          <div className="px-6 pt-5 pb-4">
            <h2 className="text-[14.5px] font-semibold tracking-[-0.01em] text-fg">Open by severity</h2>
            <p className="mt-0.5 text-[12.5px] text-muted">Select one to filter the table</p>
          </div>
          <div className="flex flex-1 flex-col divide-y divide-line border-t border-line">
            {(['HIGH', 'MEDIUM', 'LOW'] as const).map((s) => {
              const n = (all.data ?? []).filter((a) => a.status === 'active' && a.severity === s).length
              return (
                <button key={s} type="button" aria-pressed={sev === s} onClick={() => { setStatus('active'); setSev(sev === s ? '' : s) }}
                        className={`flex flex-1 items-center justify-between px-6 py-4 text-left transition-colors last:rounded-b-xl hover:bg-subtle ${sev === s ? 'bg-subtle' : ''}`}>
                  <StatusBadge value={s} />
                  <span className={`figure text-[1.75rem] ${s === 'HIGH' && n ? 'text-accent' : 'text-fg'}`}>{n}</span>
                </button>
              )
            })}
          </div>
        </Panel>
        <Card title="Score distribution" subtitle={`${sample} · mean confidence ${insight.meanConfidence ?? '—'}%`} className="lg:col-span-4" index={0}>
          {all.isLoading ? <Skeleton className="h-32" /> : !insight.bins.length ? <Empty title="No scores yet" /> : (
            <Columns data={insight.bins} label="Anomalies by score" height={120} />
          )}
        </Card>
        <Card title="By type" subtitle="All statuses" className="lg:col-span-4" index={1}>
          {all.isLoading ? <Skeleton className="h-32" /> : !insight.byType.length ? <Empty title="No detections yet" /> : (
            <div className="-mx-2"><BarList rows={insight.byType.map(([k, v]) => ({ key: k, label: humanize(k), value: v }))} /></div>
          )}
        </Card>
        <Card title="Detection source" subtitle="Rule set or model version" className="lg:col-span-4" index={2}>
          {all.isLoading ? <Skeleton className="h-32" /> : !insight.byModel.length ? <Empty title="No detections yet" /> : (
            <div className="-mx-2">
              <BarList rows={insight.byModel.map(([k, v]) => ({ key: k, label: <span className="font-mono text-[12px]">{k}</span>, value: v }))} />
            </div>
          )}
        </Card>
      </Grid>

      <div className="mb-4 flex flex-wrap items-center gap-3">
        <Segmented label="Status" value={status} onChange={setStatus} options={STATUSES.map((s) => ({ ...s, count: counts[s.value] ?? 0 }))} />
        <Segmented
          label="Severity"
          value={sev}
          onChange={setSev}
          options={[{ value: '', label: 'Any' }, { value: 'HIGH', label: 'High' }, { value: 'MEDIUM', label: 'Medium' }, { value: 'LOW', label: 'Low' }]}
        />
      </div>

      <Card title={`${rows.length} anomal${rows.length === 1 ? 'y' : 'ies'}`} subtitle="Select a row for details" flush>
        {list.error ? <div className="px-6 pb-6"><ErrorBox error={list.error} /></div> : list.isLoading ? (
          <div className="space-y-2 px-6 pb-6">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-10" />)}</div>
        ) : !rows.length ? (
          <Empty title={status === 'active' ? 'All clear' : 'Nothing here'}>
            {status === 'active' ? 'No active anomalies match these filters.' : 'No anomalies match these filters.'}
          </Empty>
        ) : (
          <Table head={<><th className="th">Severity</th><th className="th">Type / reason</th><th className="th">Resource</th><th className="th text-right">Score</th><th className="th text-right">Confidence</th><th className="th">Status</th><th className="th text-right">Detected</th></>}>
            {rows.map((a) => (
              <tr key={a.id} onClick={() => setOpenId(a.id)} className="cursor-pointer transition-colors hover:bg-subtle">
                <td className="td"><StatusBadge value={a.severity} /></td>
                <td className="td max-w-md">
                  <RowButton onClick={() => setOpenId(a.id)}>{humanize(a.anomaly_type)}</RowButton>
                  <div className="truncate text-[12.5px] text-muted">{a.reason}</div>
                </td>
                <td className="td text-fg-2">{a.resource_id ? names.get(a.resource_id) ?? a.resource_id.slice(0, 8) : '—'}</td>
                <td className="td text-right">{num(a.anomaly_score)}</td>
                <td className="td text-right text-fg-2">{a.confidence != null ? `${Math.round(a.confidence * 100)}%` : '—'}</td>
                <td className="td"><StatusBadge value={a.status} /></td>
                <td className="td text-right whitespace-nowrap text-fg-2">{relTime(a.detected_at)}</td>
              </tr>
            ))}
          </Table>
        )}
      </Card>

      <AnomalyDrawer a={open} resourceName={open?.resource_id ? names.get(open.resource_id) : undefined} onClose={() => setOpenId('')} />
    </>
  )
}
