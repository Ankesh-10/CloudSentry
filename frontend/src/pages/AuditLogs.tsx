import { useMemo, useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'

import { Columns, shortDay } from '../components/charts'
import { Drawer } from '../components/Dialog'
import { Button, Card, Empty, ErrorBox, Grid, Json, KeyValue, PageHeader, Panel, RowButton, Segmented, Skeleton, StatusBadge, Table } from '../components/ui'
import { useAuditLogs, type AuditFilters } from '../hooks/queries'
import { useUrlState } from '../hooks/useUrlState'
import { dateTime, humanize } from '../lib/format'
import type { AuditLog } from '../lib/types'

const PAGE = 50
const HOUR = 3600_000
const RANGES = [
  { value: '24h', label: '24 hours', ms: 24 * HOUR },
  { value: '7d', label: '7 days', ms: 7 * 24 * HOUR },
  { value: '30d', label: '30 days', ms: 30 * 24 * HOUR },
  { value: 'all', label: 'All', ms: 0 },
]

// Event types the backend writes; the list is extended with whatever appears in the data.
const KNOWN_EVENTS = [
  'action_approved', 'action_executed', 'action_failed', 'action_rolled_back', 'anomaly_status_changed',
  'config_change_attempted', 'config_change_requested', 'config_changed', 'discovery_completed', 'emergency_stop',
]

function buckets(rows: AuditLog[], range: string) {
  const hourly = range === '24h'
  const n = hourly ? 24 : range === '7d' ? 7 : 30
  const step = hourly ? HOUR : 24 * HOUR
  const end = new Date()
  if (hourly) end.setUTCMinutes(0, 0, 0)
  else end.setUTCHours(0, 0, 0, 0)
  const t0 = end.getTime() - (n - 1) * step
  const out = Array.from({ length: n }, (_, i) => ({ key: new Date(t0 + i * step).toISOString(), value: 0 }))
  for (const r of rows) {
    const i = Math.floor((new Date(r.created_at).getTime() - t0) / step)
    if (i >= 0 && i < n) out[i].value += 1
  }
  return { data: out, hourly }
}

export default function AuditLogs() {
  const [range, setRange] = useUrlState('range', '7d')
  const [event, setEvent] = useUrlState('event')
  const [actor, setActor] = useUrlState('actor')
  const [rid, setRid] = useUrlState('rid')
  const [pageStr, setPage] = useUrlState('page', '0')
  const [openId, setOpenId] = useState<string | null>(null)
  const [draft, setDraft] = useState({ actor, rid })
  const page = Number(pageStr) || 0

  // Fixed when the range is chosen so the query key stays stable between renders.
  const since = useMemo(() => {
    const r = RANGES.find((x) => x.value === range)
    return r?.ms ? new Date(Math.floor(Date.now() / 60_000) * 60_000 - r.ms).toISOString() : undefined
  }, [range])
  const filters: AuditFilters = { event_type: event || undefined, actor: actor || undefined, request_id: rid || undefined, since }

  const list = useAuditLogs(filters, page * PAGE, PAGE)
  const sample = useAuditLogs(filters, 0, 500)
  const chart = useMemo(() => buckets(sample.data ?? [], range === 'all' ? '30d' : range), [sample.data, range])
  const events = useMemo(
    () => [...new Set([...KNOWN_EVENTS, ...(sample.data ?? []).map((r) => r.event_type ?? ''), event].filter(Boolean))].sort(),
    [sample.data, event],
  )
  const rows = list.data ?? []
  const open = rows.find((r) => r.id === openId) ?? null
  const errors = (sample.data ?? []).filter((r) => r.response_status && !['success', 'ok', 'persisted'].includes(r.response_status)).length

  function apply(e: FormEvent) {
    e.preventDefault()
    setActor(draft.actor.trim())
    setRid(draft.rid.trim())
  }

  return (
    <>
      <PageHeader
        title="Audit log"
        kicker="Ledger"
        subtitle="Append-only record of every config change, approval and cloud call. Rows cannot be edited or deleted for 90 days."
        actions={<Segmented label="Time range" value={range} onChange={setRange} options={RANGES} />}
      />

      <Grid className="mb-5 lg:grid-cols-12">
        <Card
          title={`Events per ${chart.hourly ? 'hour' : 'day'}`}
          subtitle={sample.data && sample.data.length >= 500 ? 'Latest 500 events in range' : `${sample.data?.length ?? 0} events in range`}
          className="lg:col-span-9"
          index={0}
        >
          {sample.isLoading ? <Skeleton className="h-28" /> : (
            <Columns
              data={chart.data}
              label="Audit events over time"
              height={120}
              dateFormat={(k) => (chart.hourly ? `${k.slice(11, 13)}:00 UTC` : shortDay(k.slice(0, 10)))}
            />
          )}
        </Card>
        <Panel className="flex flex-col justify-between px-6 py-5 lg:col-span-3" index={1}>
          <div className="text-[12.5px] text-muted">Non-success responses</div>
          <div className={`figure mt-3 text-[2.75rem] ${errors ? 'text-accent' : 'text-fg'}`}>{sample.isLoading ? '—' : errors}</div>
          <div className="mt-2 text-[12.5px] text-muted">
            {sample.data?.length ? `${Math.round((errors / sample.data.length) * 100)}% of sampled events` : 'In the sampled window'}
          </div>
        </Panel>
      </Grid>

      <form onSubmit={apply} className="mb-4 flex flex-wrap items-center gap-2">
        <select className="input" value={event} onChange={(e) => setEvent(e.target.value)} aria-label="Event type">
          <option value="">All events</option>
          {events.map((ev) => <option key={ev} value={ev}>{ev}</option>)}
        </select>
        <input className="input w-44" placeholder="Actor" value={draft.actor} onChange={(e) => setDraft({ ...draft, actor: e.target.value })} aria-label="Actor" />
        <input className="input w-44" placeholder="Request id" value={draft.rid} onChange={(e) => setDraft({ ...draft, rid: e.target.value })} aria-label="Request id" />
        <Button type="submit" variant="primary">Filter</Button>
        {(event || actor || rid) && (
          <Button variant="ghost" onClick={() => { setEvent(''); setActor(''); setRid(''); setDraft({ actor: '', rid: '' }) }}>Clear</Button>
        )}
      </form>

      <Card
       
        title={list.isFetching ? 'Loading…' : rows.length ? `Rows ${page * PAGE + 1}–${page * PAGE + rows.length}` : 'No rows'}
        subtitle="Newest first · select a row for parameters"
        flush
        actions={
          <>
            <Button size="sm" disabled={page === 0} onClick={() => setPage(String(page - 1))}>Newer</Button>
            <Button size="sm" disabled={rows.length < PAGE} onClick={() => setPage(String(page + 1))}>Older</Button>
          </>
        }
      >
        {list.error ? <div className="px-6 pb-6"><ErrorBox error={list.error} /></div> : list.isLoading ? (
          <div className="space-y-2 px-6 pb-6">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-9" />)}</div>
        ) : !rows.length ? (
          <Empty title="No entries">No audit entries match these filters.</Empty>
        ) : (
          <Table head={<><th className="th">Time (local)</th><th className="th">Event</th><th className="th">Actor</th><th className="th">Result</th><th className="th">Message</th><th className="th">Request</th></>}>
            {rows.map((l) => (
              <tr key={l.id} className="cursor-pointer transition-colors hover:bg-subtle" onClick={() => setOpenId(l.id)}>
                <td className="td whitespace-nowrap text-fg-2">{dateTime(l.created_at)}</td>
                <td className="td"><RowButton onClick={() => setOpenId(l.id)}><code className="font-mono text-[12.5px]">{l.event_type ?? '—'}</code></RowButton></td>
                <td className="td">{l.actor ?? '—'}</td>
                <td className="td">{l.response_status ? <StatusBadge value={l.response_status} /> : <span className="text-muted">—</span>}</td>
                <td className="td max-w-md truncate text-fg-2">{l.message ?? humanize(l.aws_api_call)}</td>
                <td className="td"><samp className="font-mono text-[12px] text-muted">{l.request_id?.slice(0, 10) ?? '—'}</samp></td>
              </tr>
            ))}
          </Table>
        )}
      </Card>

      {open && (
        <Drawer open onOpenChange={(o) => !o && setOpenId(null)} eyebrow={`Audit ${open.id.slice(0, 8)}`} title={open.event_type ?? 'Event'} subtitle={dateTime(open.created_at)}>
          {open.message && <div className="mb-5 rounded-lg bg-subtle px-4 py-3 text-[13.5px] text-fg-2">{open.message}</div>}
          <KeyValue
            items={[
              ['Actor', open.actor ?? '—'],
              ['Result', open.response_status ? <StatusBadge value={open.response_status} /> : '—'],
              ['Cloud call', open.aws_api_call ?? '—'],
              ['Resource', open.resource_id ? <Link className="font-medium text-info hover:underline" to={`/resources/${open.resource_id}`}>{open.resource_id.slice(0, 8)}</Link> : '—'],
              ['Action', open.action_id ? <Link className="font-medium text-info hover:underline" to={`/actions?id=${open.action_id}`}>{open.action_id.slice(0, 8)}</Link> : '—'],
              ['Request id', open.request_id ?? '—'],
              ['Client IP', open.client_ip ?? '—'],
            ]}
          />
          <div className="eyebrow mt-6 mb-2 text-muted">Request parameters</div>
          <Json value={open.request_params} />
          {open.request_id && (
            <Button className="mt-4" onClick={() => { setRid(open.request_id!); setDraft({ ...draft, rid: open.request_id! }); setOpenId(null) }}>
              Show all rows for this request
            </Button>
          )}
        </Drawer>
      )}
    </>
  )
}
