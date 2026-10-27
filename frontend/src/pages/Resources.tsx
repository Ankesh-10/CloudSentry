import { useMemo } from 'react'
import { Link, useNavigate } from 'react-router-dom'

import { toast } from '../components/Toast'
import { Badge, Button, Card, Empty, ErrorBox, PageHeader, Segmented, Skeleton, StatusBadge, Table, Tag, Tip } from '../components/ui'
import { useDiscover, useResources } from '../hooks/queries'
import { useUrlState } from '../hooks/useUrlState'
import { relTime } from '../lib/format'
import { READ_ONLY_HINT, useCanOperate } from '../lib/role'

export default function Resources() {
  const [type, setType] = useUrlState('type')
  const [q, setQ] = useUrlState('q')
  const [deleted, setDeleted] = useUrlState('deleted')
  const navigate = useNavigate()
  const canOperate = useCanOperate()
  // One fetch for all types: the type chips need counts, filtering is local.
  const { data, error, isLoading, isFetching } = useResources({ include_deleted: deleted === '1' })
  const discover = useDiscover()

  const counts = useMemo(() => {
    const m = new Map<string, number>()
    for (const r of data ?? []) m.set(r.resource_type, (m.get(r.resource_type) ?? 0) + 1)
    return [...m.entries()].sort((a, b) => b[1] - a[1])
  }, [data])

  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase()
    return (data ?? []).filter(
      (r) =>
        (!type || r.resource_type === type) &&
        (!needle || [r.name, r.provider_id, r.region, r.state].some((v) => v?.toLowerCase().includes(needle))),
    )
  }, [data, type, q])

  return (
    <>
      <PageHeader
        title="Resources"
        kicker="Inventory"
        subtitle="Everything discovered in your cloud accounts. Discovery runs every 15 minutes."
        actions={
          <Tip content={canOperate ? 'Operator only · max 2 per minute' : READ_ONLY_HINT}>
            <span>
              <Button
                variant="primary"
                disabled={discover.isPending || !canOperate}
                onClick={() =>
                  discover.mutate(undefined, {
                    onSuccess: () => toast.success('Discovery finished', 'Inventory refreshed.'),
                    onError: (e) => toast.error('Discovery failed', e.message),
                  })
                }
              >
                {discover.isPending ? 'Discovering…' : 'Run discovery'}
              </Button>
            </span>
          </Tip>
        }
      />

      <div className="mb-4 flex flex-wrap items-center gap-3">
        <Segmented
          label="Resource type"
          value={type}
          onChange={setType}
          options={[{ value: '', label: 'All', count: data?.length }, ...counts.map(([k, n]) => ({ value: k, label: k, count: n }))]}
        />
        <input className="input w-64" placeholder="Search name, id, region" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search resources" />
        <label className="flex cursor-pointer items-center gap-2 text-[13px] text-fg-2">
          <input type="checkbox" className="h-4 w-4 rounded accent-[var(--primary)]" checked={deleted === '1'} onChange={(e) => setDeleted(e.target.checked ? '1' : '')} />
          Include deleted
        </label>
      </div>

      <Card
       
        title={`${rows.length} resource${rows.length === 1 ? '' : 's'}`}
        subtitle={isFetching && !isLoading ? 'Refreshing…' : type ? `Filtered: ${type}` : 'All types'}
        flush
      >
        {error ? (
          <div className="px-6 pb-6"><ErrorBox error={error} /></div>
        ) : isLoading ? (
          <div className="space-y-2 px-6 pb-6">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-9" />)}</div>
        ) : !rows.length ? (
          <Empty
            title={data?.length ? 'No matches' : 'Nothing discovered yet'}
            action={data?.length ? <Button onClick={() => { setQ(''); setType('') }}>Clear filters</Button> : undefined}
          >
            {data?.length ? 'No resource matches these filters.' : 'Run discovery, or check the backend’s cloud credentials.'}
          </Empty>
        ) : (
          <Table
            head={
              <>
                                <th className="th">Resource</th>
                <th className="th">Type</th>
                <th className="th">Region</th>
                <th className="th">State</th>
                <th className="th">Flags</th>
                <th className="th text-right">Last seen</th>
              </>
            }
          >
            {rows.map((r) => (
              <tr key={r.id} className="cursor-pointer transition-colors hover:bg-subtle" onClick={() => navigate(`/resources/${r.id}`)}>
                                <td className="td">
                  <Link to={`/resources/${r.id}`} onClick={(e) => e.stopPropagation()} className="font-medium text-fg hover:underline">
                    {r.name || r.provider_id}
                  </Link>
                  <samp className="block font-mono text-[12px] text-muted">{r.provider_id}</samp>
                </td>
                <td className="td"><Tag>{r.resource_type}</Tag></td>
                <td className="td text-fg-2">{r.region ?? '—'}</td>
                <td className="td"><StatusBadge value={r.state} /></td>
                <td className="td">{r.protected ? <Badge tone="accent">Protected</Badge> : <span className="text-muted">—</span>}</td>
                <td className="td text-right whitespace-nowrap text-fg-2">{relTime(r.last_seen)}</td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
    </>
  )
}
