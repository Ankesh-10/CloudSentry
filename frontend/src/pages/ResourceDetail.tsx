import { useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import { ActionModeBadge } from '../components/ActionMode'
import { TrendArea } from '../components/charts'
import { ArrowLeftIcon, PlusIcon } from '@phosphor-icons/react'

import { Badge, Card, Empty, ErrorBox, Grid, Json, KeyValue, Loading, PageHeader, Panel, Segmented, Skeleton, StatusBadge, Table, Tag } from '../components/ui'
import { useMetrics, useResource } from '../hooks/queries'
import { dateTime, humanize, num, relTime, usd } from '../lib/format'
import type { Anomaly } from '../lib/types'

const RANGES = [
  { value: 1, label: '24 hours' },
  { value: 7, label: '7 days' },
  { value: 30, label: '30 days' },
]

function Telemetry({ id, names, anomalies }: { id: string; names: string[]; anomalies: Anomaly[] }) {
  const [days, setDays] = useState(1)
  const [metric, setMetric] = useState('')
  const selected = metric || names[0] || ''
  const { data, error, isLoading, isFetching } = useMetrics(id, days, selected || undefined)

  const series = useMemo(
    () => (data ?? []).map((p) => ({ t: new Date(p.time).getTime(), value: p.value })).sort((a, b) => a.t - b.t),
    [data],
  )
  const unit = data?.[0]?.unit ?? ''
  // Anomalies detected inside the visible window become red markers.
  const markers = useMemo(() => {
    if (!series.length) return []
    const [lo, hi] = [series[0].t, series[series.length - 1].t]
    return anomalies
      .filter((a) => a.detected_at)
      .map((a) => ({ x: new Date(a.detected_at!).getTime(), label: a.severity ? a.severity[0] + a.severity.slice(1).toLowerCase() : 'Anomaly' }))
      .filter((m) => m.x >= lo && m.x <= hi)
  }, [anomalies, series])
  const fmt = (v: number) => `${num(v)}${unit === 'Percent' ? '%' : ''}`
  const tick = (t: string | number) =>
    new Date(Number(t)).toLocaleString([], days > 1 ? { month: 'short', day: 'numeric' } : { hour: '2-digit', minute: '2-digit', hour12: false })

  return (
    <Card
     
      title="Telemetry"
      subtitle={selected ? `${selected} · ${unit || 'value'} · ${markers.length} anomaly marker${markers.length === 1 ? '' : 's'}` : 'No metrics'}
      className="lg:col-span-8"
      actions={<Segmented label="Range" value={days} onChange={setDays} options={RANGES} />}
    >
      {names.length > 1 && (
        <div className="mb-4">
          <Segmented label="Metric" value={selected} onChange={setMetric} options={names.map((n) => ({ value: n, label: n }))} />
        </div>
      )}
      {!names.length ? (
        <Empty title="No telemetry">No metrics in the last 24 h. Telemetry is collected every 5 minutes for EC2, Lambda and RDS.</Empty>
      ) : error ? (
        <ErrorBox error={error} />
      ) : isLoading ? (
        <Skeleton className="h-64" />
      ) : !series.length ? (
        <Empty title="No samples">Nothing recorded in this window.</Empty>
      ) : (
        <div className={`transition-opacity ${isFetching ? 'opacity-60' : ''}`}>
          <TrendArea data={series} x="t" y="value" time name={selected} format={fmt} xFormat={tick} markers={markers} height={280} />
        </div>
      )}
    </Card>
  )
}

export default function ResourceDetail() {
  const { id = '' } = useParams()
  const { data, error, isLoading } = useResource(id)

  const latest = useMemo(() => {
    const m = new Map<string, { value: number; unit: string | null; time: string }>()
    for (const p of data?.metrics_24h ?? []) {
      const cur = m.get(p.metric_name)
      if (!cur || p.time > cur.time) m.set(p.metric_name, { value: p.value, unit: p.unit, time: p.time })
    }
    return [...m.entries()].sort(([a], [b]) => a.localeCompare(b))
  }, [data])

  if (error) return <ErrorBox error={error} />
  if (isLoading || !data) return <Loading label="Loading resource" />

  const r = data.resource
  const names = latest.map(([n]) => n)

  return (
    <>
      <Link to="/resources" className="mb-2 inline-flex items-center gap-1.5 text-[13px] text-muted hover:text-fg"><ArrowLeftIcon size={14} /> Resources</Link>
      <PageHeader
        title={r.name || r.provider_id}
        kicker={`Resource / ${r.resource_type}`}
        subtitle={`${r.provider_id} · ${r.region ?? 'unknown region'}`}
        actions={
          <>
            {r.protected && <Badge tone="accent">Protected</Badge>}
            <StatusBadge value={r.state} />
          </>
        }
      />

      {latest.length > 0 && (
        <Grid className={`mb-5 grid-cols-2 ${['md:grid-cols-1', 'md:grid-cols-2', 'md:grid-cols-3', 'md:grid-cols-4'][Math.min(latest.length, 4) - 1]} ${latest.length === 1 ? 'grid-cols-1' : ''}`}>
          {latest.slice(0, 4).map(([name, v], i) => (
            <Panel key={name} className="px-6 py-5" index={i}>
              <div className="text-[12.5px] text-muted">{name} · latest</div>
              <div className="figure mt-3 text-[1.9rem] text-fg">
                <data value={v.value}>{num(v.value)}</data>
                <span className="ml-1 text-[0.95rem] font-medium tracking-normal text-muted">{v.unit === 'Percent' ? '%' : v.unit === 'Milliseconds' ? 'ms' : ''}</span>
              </div>
              <div className="mt-2 text-[12.5px] text-muted">{relTime(v.time)}</div>
            </Panel>
          ))}
        </Grid>
      )}

      <Grid className="lg:grid-cols-12">
        <Telemetry id={id} names={names} anomalies={data.anomalies} />
        <Card title="Details" className="lg:col-span-4">
          <KeyValue
            items={[
              ['Provider id', <samp className="break-all">{r.provider_id}</samp>],
              ['Type', <Tag>{r.resource_type}</Tag>],
              ['Region', r.region ?? '—'],
              ['State', <StatusBadge value={r.state} />],
              ['First seen', dateTime(r.first_seen)],
              ['Last seen', relTime(r.last_seen)],
            ]}
          />
          <div className="eyebrow mt-5 mb-2 text-muted">Tags</div>
          <div className="flex flex-wrap gap-1">
            {r.tags && Object.keys(r.tags).length
              ? Object.entries(r.tags).map(([k, v]) => <Tag key={k}>{k}={String(v)}</Tag>)
              : <span className="text-[13px] text-muted">None</span>}
          </div>
          {r.metadata && Object.keys(r.metadata).length > 0 && (
            <details className="mt-4">
              <summary className="flex cursor-pointer list-none items-center gap-1.5 border-b border-line py-2 text-[13px] font-medium text-fg-2 hover:text-fg [&::-webkit-details-marker]:hidden"><PlusIcon size={13} /> Metadata</summary>
              <div className="mt-2"><Json value={r.metadata} /></div>
            </details>
          )}
        </Card>

        <Card title="Anomalies" subtitle={`Latest ${data.anomalies.length}`} className="lg:col-span-6" flush>
          {!data.anomalies.length ? <Empty title="None">No anomalies recorded for this resource.</Empty> : (
            <Table head={<><th className="th">Severity</th><th className="th">Type</th><th className="th">Status</th><th className="th text-right">Detected</th></>}>
              {data.anomalies.map((a) => (
                <tr key={a.id} className="hover:bg-subtle">
                  <td className="td"><StatusBadge value={a.severity} /></td>
                  <td className="td" title={a.reason ?? ''}>
                    <div className="font-medium text-fg">{humanize(a.anomaly_type)}</div>
                    <div className="max-w-xs truncate text-[12.5px] text-muted">{a.reason}</div>
                  </td>
                  <td className="td"><StatusBadge value={a.status} /></td>
                  <td className="td text-right whitespace-nowrap text-fg-2">{relTime(a.detected_at)}</td>
                </tr>
              ))}
            </Table>
          )}
        </Card>
        <Card title="Actions" subtitle={`Latest ${data.actions.length}`} className="lg:col-span-6" flush>
          {!data.actions.length ? <Empty title="None">No actions proposed for this resource.</Empty> : (
            <Table head={<><th className="th">Action</th><th className="th">Status</th><th className="th text-right">Savings/mo</th><th className="th text-right">Created</th></>}>
              {data.actions.map((a) => (
                <tr key={a.id} className="hover:bg-subtle">
                  <td className="td">
                    <span className="font-medium text-fg">{humanize(a.action_type)}</span>
                    <span className="ml-2"><ActionModeBadge action={a} /></span>
                  </td>
                  <td className="td"><StatusBadge value={a.status} /></td>
                  <td className="td text-right">{usd(a.estimated_savings_usd)}</td>
                  <td className="td text-right whitespace-nowrap text-fg-2">{relTime(a.created_at)}</td>
                </tr>
              ))}
            </Table>
          )}
        </Card>
      </Grid>
    </>
  )
}
