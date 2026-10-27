import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'

import { ActionModeBadge } from '../components/ActionMode'
import { ActionWhy } from '../components/ActionWhy'
import { ConfirmDialog, Drawer } from '../components/Dialog'
import { SavingsPanel } from '../components/insights'
import { toast } from '../components/Toast'
import { Button, Card, Empty, ErrorBox, Grid, Json, KeyValue, PageHeader, Panel, RowButton, Segmented, Skeleton, Stat, StatusBadge, Table, Tip } from '../components/ui'
import { useActionOp, useActions, useDashboardSummary, useResources, type ActionOp } from '../hooks/queries'
import { useUrlState } from '../hooks/useUrlState'
import { effectOf } from '../lib/consequence'
import { dateTime, humanize, relTime, usd } from '../lib/format'
import { READ_ONLY_HINT, useCanOperate } from '../lib/role'
import { REVERSIBLE_ACTIONS, type Action, type ActionStatus } from '../lib/types'

// Mirrors the guards in backend/app/api/actions.py so only valid buttons show;
// the server re-checks everything.
export function availableOps(a: Action): ActionOp[] {
  const ops: ActionOp[] = []
  if (a.status === 'pending' || a.status === 'pending_approval') ops.push('approve', 'reject')
  if (a.status === 'approved' || (a.status === 'pending' && !a.requires_approval)) ops.push('execute')
  if (a.status === 'completed' && !a.rollback_action_id && a.action_type && REVERSIBLE_ACTIONS.has(a.action_type)) {
    ops.push('rollback')
  }
  return ops
}

const TABS: { value: ActionStatus | ''; label: string }[] = [
  { value: '', label: 'All' },
  { value: 'pending_approval', label: 'Awaiting' },
  { value: 'pending', label: 'Pending' },
  { value: 'approved', label: 'Approved' },
  { value: 'executing', label: 'Running' },
  { value: 'completed', label: 'Done' },
  { value: 'failed', label: 'Failed' },
  { value: 'rejected', label: 'Rejected' },
  { value: 'rolled_back', label: 'Rolled back' },
]

const OP_LABEL: Record<ActionOp, string> = { approve: 'Approve', reject: 'Reject', execute: 'Execute', rollback: 'Roll back' }

// `full`: phone cards, where each operation gets an equal, 44px-tall button.
function OpButtons({ a, onOp, size = 'sm', full = false }: { a: Action; onOp: (op: ActionOp) => void; size?: 'sm' | 'md'; full?: boolean }) {
  const canOperate = useCanOperate()
  const ops = availableOps(a)
  if (!ops.length) return null
  return (
    <Tip content={canOperate ? null : READ_ONLY_HINT}>
      <span className={full ? 'grid auto-cols-fr grid-flow-col gap-2' : 'flex gap-1.5'} onClick={(e) => e.stopPropagation()}>
        {ops.map((op) => (
          <Button key={op} size={size} disabled={!canOperate} variant={op === 'approve' || op === 'execute' ? 'primary' : op === 'rollback' ? 'danger-outline' : 'secondary'} className={full ? 'h-11 w-full' : ''} onClick={() => onOp(op)}>
            {OP_LABEL[op]}
          </Button>
        ))}
      </span>
    </Tip>
  )
}

export default function Actions() {
  const [status, setStatus] = useUrlState('status')
  const [openId, setOpenId] = useUrlState('id')
  const all = useActions({ limit: 500 })
  const list = useActions({ status: (status || undefined) as ActionStatus | undefined, limit: 500 })
  const resources = useResources({ include_deleted: true })
  const summary = useDashboardSummary()
  const op = useActionOp()
  const [pending, setPending] = useState<{ a: Action; op: ActionOp } | null>(null)

  const names = useMemo(() => new Map((resources.data ?? []).map((r) => [r.id, r.name || r.provider_id])), [resources.data])
  const counts = useMemo(() => {
    const c: Record<string, number> = { '': all.data?.length ?? 0 }
    for (const a of all.data ?? []) c[a.status ?? 'pending'] = (c[a.status ?? 'pending'] ?? 0) + 1
    return c
  }, [all.data])
  const totals = useMemo(() => {
    const d = all.data ?? []
    const sum = (f: (a: Action) => boolean) => d.filter(f).reduce((s, a) => s + (a.estimated_savings_usd ?? 0), 0)
    return {
      open: sum((a) => ['pending', 'pending_approval', 'approved'].includes(a.status ?? '')),
      done: sum((a) => a.status === 'completed' && !a.dry_run),
      dry: sum((a) => a.status === 'completed' && !!a.dry_run),
    }
  }, [all.data])
  const rows = list.data ?? []
  const open = rows.find((a) => a.id === openId) ?? (all.data ?? []).find((a) => a.id === openId) ?? null
  const effect = pending ? effectOf(pending.op, pending.a, summary.data) : null

  function run() {
    if (!pending) return
    const { a, op: o } = pending
    op.mutate(
      { id: a.id, op: o },
      {
        onSuccess: () => {
          setPending(null)
          toast.success(`${OP_LABEL[o]}: ${humanize(a.action_type)}`, o === 'execute' || o === 'rollback' ? 'Queued — status updates as the runner works through the safety checks.' : undefined)
        },
        onError: (e) => { setPending(null); toast.error(`Could not ${OP_LABEL[o].toLowerCase()}`, e.message) },
      },
    )
  }

  return (
    <>
      <PageHeader
        title="Actions"
        kicker="Remediation"
        subtitle="Proposals from the policy engine. Approve, execute and roll back need the operator role; each passes the safety layer first."
      />

      <Grid className="mb-5 lg:grid-cols-12">
        <Panel className="flex flex-col divide-y divide-line lg:col-span-4" index={0}>
          {[
            { label: 'Open proposals', value: usd(totals.open), hint: `${(counts.pending ?? 0) + (counts.pending_approval ?? 0) + (counts.approved ?? 0)} awaiting a decision or run` },
            { label: 'Realized (live)', value: usd(totals.done), hint: `${counts.completed ?? 0} completed · monthly estimate` },
            { label: 'Dry-run validated', value: usd(totals.dry), hint: 'Would have saved, if run live' },
          ].map((s) => <Stat key={s.label} label={s.label} value={all.isLoading ? '—' : s.value} hint={s.hint} />)}
        </Panel>
        <SavingsPanel range={30} className="lg:col-span-8" index={1} />
      </Grid>

      <div className="mb-4">
        <Segmented label="Status" value={status} onChange={setStatus} options={TABS.map((t) => ({ ...t, count: counts[t.value] ?? 0 }))} />
      </div>

      <Card title={`${rows.length} action${rows.length === 1 ? '' : 's'}`} subtitle="Select a row for pre/post state" flush>
        {list.error ? <div className="px-6 pb-6"><ErrorBox error={list.error} /></div> : list.isLoading ? (
          <div className="space-y-2 px-6 pb-6">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-9" />)}</div>
        ) : !rows.length ? (
          <Empty title="Nothing here">No actions with this status.</Empty>
        ) : (
          <>
          {/* Phones: one card per action, decisions full width and within thumb reach. */}
          <ul className="divide-y divide-line border-t border-line md:hidden">
            {rows.map((a) => (
              <li key={a.id} className="px-5 py-4">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <RowButton onClick={() => setOpenId(a.id)}>{humanize(a.action_type)}</RowButton>
                    <div className="mt-0.5 truncate text-[12.5px] text-muted">
                      {a.resource_id ? names.get(a.resource_id) ?? a.resource_id.slice(0, 8) : '—'} · {relTime(a.created_at)}
                    </div>
                  </div>
                  <span className="shrink-0 text-[13.5px] font-medium text-fg tabular-nums">{usd(a.estimated_savings_usd)}</span>
                </div>
                <div className="mt-2 flex flex-wrap gap-1.5">
                  <ActionModeBadge action={a} />
                  <StatusBadge value={a.risk_level?.toUpperCase()} />
                  <StatusBadge value={a.status} />
                </div>
                {availableOps(a).length > 0 && <div className="mt-3"><OpButtons a={a} size="md" full onOp={(o) => setPending({ a, op: o })} /></div>}
              </li>
            ))}
          </ul>
          <div className="max-md:hidden">
          <Table head={<><th className="th">Action</th><th className="th">Resource</th><th className="th">Risk</th><th className="th">Status</th><th className="th text-right">Savings/mo</th><th className="th text-right">Created</th><th className="th" /></>}>
            {rows.map((a) => (
              <tr key={a.id} onClick={() => setOpenId(a.id)} className="cursor-pointer transition-colors hover:bg-subtle">
                <td className="td">
                  <RowButton onClick={() => setOpenId(a.id)}>{humanize(a.action_type)}</RowButton>
                  <span className="ml-2"><ActionModeBadge action={a} /></span>
                </td>
                <td className="td text-fg-2">{a.resource_id ? names.get(a.resource_id) ?? a.resource_id.slice(0, 8) : '—'}</td>
                <td className="td"><StatusBadge value={a.risk_level?.toUpperCase()} /></td>
                <td className="td"><StatusBadge value={a.status} /></td>
                <td className="td text-right">{usd(a.estimated_savings_usd)}</td>
                <td className="td text-right whitespace-nowrap text-fg-2">{relTime(a.created_at)}</td>
                <td className="td text-right"><OpButtons a={a} onOp={(o) => setPending({ a, op: o })} /></td>
              </tr>
            ))}
          </Table>
          </div>
          </>
        )}
      </Card>

      {open && (
        <Drawer open onOpenChange={(o) => !o && setOpenId('')} eyebrow={`Action ${open.id.slice(0, 8)}`} title={humanize(open.action_type)}
                subtitle={`${open.dry_run ? 'Dry run' : 'LIVE'} · ${open.risk_level ?? 'unknown'} risk · created ${relTime(open.created_at)}`}>
          <KeyValue
            items={[
              ['Status', <StatusBadge value={open.status} />],
              ['Resource', open.resource_id ? <Link className="font-medium text-info hover:underline" to={`/resources/${open.resource_id}`}>{names.get(open.resource_id) ?? open.resource_id.slice(0, 8)}</Link> : '—'],
              ['Savings', `${usd(open.estimated_savings_usd)} / month`],
              ['Needs approval', open.requires_approval ? 'Yes' : 'No'],
              ['Approved by', open.approved_by ?? '—'],
              ['Approved', dateTime(open.approved_at)],
              ['Executed', dateTime(open.executed_at)],
              ['Verified', dateTime(open.verified_at)],
              ['Rollback', open.rollback_action_id ? open.rollback_action_id.slice(0, 8) : '—'],
            ]}
          />
          <div className="mt-5 grid gap-4 sm:grid-cols-2">
            <div><div className="eyebrow mb-2 text-muted">Pre-state</div><Json value={open.pre_state} /></div>
            <div><div className="eyebrow mb-2 text-muted">Post-state</div><Json value={open.post_state} /></div>
          </div>
          <div className="mt-6 border-t border-line pt-5">
            {availableOps(open).length ? <OpButtons a={open} size="md" onOp={(o) => setPending({ a: open, op: o })} /> : <span className="text-[13px] text-muted">No operations available in this state.</span>}
          </div>
        </Drawer>
      )}

      <ConfirmDialog
        open={!!pending}
        onOpenChange={(o) => !o && setPending(null)}
        title={pending ? `${OP_LABEL[pending.op]} ${humanize(pending.a.action_type)}` : ''}
        description={pending?.a.resource_id ? `Resource: ${names.get(pending.a.resource_id) ?? pending.a.resource_id}` : undefined}
        tone={effect?.live ? 'live' : 'confirm'}
        consequence={effect?.text}
        confirmLabel={pending ? OP_LABEL[pending.op] : 'Confirm'}
        pending={op.isPending}
        onConfirm={run}
      >
        {pending && (pending.op === 'approve' || pending.op === 'execute') && <ActionWhy action={pending.a} />}
      </ConfirmDialog>
    </>
  )
}
