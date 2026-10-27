import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { useAuth } from '../auth/AuthProvider'
import { useCancelPending, useConfirmPending, usePendingChanges, useSystemHealth } from '../hooks/queries'
import { relTime } from '../lib/format'
import { READ_ONLY_HINT, useCanOperate } from '../lib/role'
import type { PendingChange } from '../lib/types'
import { ConfirmDialog } from './Dialog'
import { toast } from './Toast'
import { Badge, Button, Card, Tip } from './ui'

// A pending change in plain words, e.g. "Turn dry-run off".
export function describePending(p: PendingChange): string {
  const [kind, , field] = p.key.split(':')
  const v = String(p.value)
  if (kind === 'POLICY') return field === 'enabled' ? 'Enable a policy' : 'Let a policy run without approval'
  if (kind === 'RESOURCE') return 'Unprotect a resource'
  if (p.key === 'GLOBAL_AUTOMATION_ENABLED') return v === 'true' ? 'Turn automation on' : 'Turn automation off'
  if (p.key === 'DRY_RUN_MODE') return v === 'false' ? 'Turn dry-run off' : 'Turn dry-run on'
  return `Set ${p.key} to ${v}`
}

// One request's state for the person looking at it.
type Stage = { kind: 'expired' } | { kind: 'waiting'; until: number } | { kind: 'others' } | { kind: 'ready' }

export function stageOf(p: PendingChange, mine: boolean, now: number): Stage {
  if (p.expired || now > Date.parse(p.expires_at)) return { kind: 'expired' }
  if (!mine) return { kind: 'ready' }
  if (!p.confirmable_at) return { kind: 'others' }
  const until = Date.parse(p.confirmable_at)
  return now < until ? { kind: 'waiting', until } : { kind: 'ready' }
}

export function useIsMine() {
  const { session } = useAuth()
  const me = session ? `USER:${session.user.id}` : null
  return (p: PendingChange) => !!me && p.requested_by === me
}

// Ticks once a second, only while something on screen is counting down.
function useNow(active: boolean) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    setNow(Date.now())
    if (!active) return
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [active])
  return now
}

const mmss = (ms: number) => {
  const s = Math.max(0, Math.ceil(ms / 1000))
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

export function PendingChanges({ className = '' }: { className?: string }) {
  const { data } = usePendingChanges()
  const mode = useSystemHealth().data?.automation.two_person
  const isMine = useIsMine()
  const canOperate = useCanOperate()
  const confirm = useConfirmPending()
  const cancel = useCancelPending()
  const [open, setOpen] = useState<PendingChange | null>(null)
  const counting = (data ?? []).some((p) => isMine(p) && p.confirmable_at && Date.parse(p.confirmable_at) > Date.now())
  const now = useNow(counting)

  if (!data?.length) return null

  function withdraw(p: PendingChange) {
    cancel.mutate(p.key, {
      onSuccess: () => toast.success('Request withdrawn', describePending(p)),
      onError: (e) => toast.error('Could not withdraw the request', e.message),
    })
  }

  return (
    <Card
      title="Pending changes"
      subtitle={mode === 'solo_delay'
        ? 'Confirm your own after 15 min · expires after 1 h'
        : 'Another operator confirms by making the same change · expires after 1 h'}
      className={className}
      flush
    >
      <ul className="divide-y divide-line border-t border-line">
        {data.map((p) => {
          const mine = isMine(p)
          const stage = stageOf(p, mine, now)
          const resourceId = p.key.startsWith('RESOURCE:') ? p.key.split(':')[1] : null
          return (
            <li key={p.key} className="flex flex-wrap items-center gap-x-4 gap-y-2 px-6 py-3.5">
              <div className="min-w-0 flex-1 basis-56">
                <div className="text-[13.5px] font-medium text-fg">
                  {describePending(p)}
                  {resourceId && <Link to={`/resources/${resourceId}`} className="ml-2 text-[12.5px] font-normal text-info hover:underline">View resource</Link>}
                </div>
                <div className="mt-0.5 text-[12.5px] text-muted">
                  Requested by {mine ? 'you' : (p.requested_by ?? 'unknown').replace(/^USER:/, '')} · {relTime(p.requested_at)}
                  {stage.kind !== 'expired' && <> · expires {relTime(p.expires_at)}</>}
                </div>
              </div>
              <div className="flex shrink-0 items-center gap-2">
                {stage.kind === 'expired' && <Badge>Expired</Badge>}
                {stage.kind === 'waiting' && (
                  <span className="text-[12.5px] text-fg-2 tabular-nums" aria-live="off">Confirm in {mmss(stage.until - now)}</span>
                )}
                {stage.kind === 'others' && <span className="text-[12.5px] text-fg-2">Waiting for another operator</span>}
                <Tip content={canOperate ? null : READ_ONLY_HINT}>
                  <span className="flex gap-1.5">
                    {stage.kind !== 'expired' && stage.kind !== 'others' && (
                      <Button size="sm" variant="primary" disabled={!canOperate || stage.kind === 'waiting'} onClick={() => setOpen(p)}>Confirm</Button>
                    )}
                    <Button size="sm" variant="secondary" disabled={!canOperate || cancel.isPending} onClick={() => withdraw(p)}>
                      {stage.kind === 'expired' ? 'Clear' : 'Withdraw'}
                    </Button>
                  </span>
                </Tip>
              </div>
            </li>
          )
        })}
      </ul>
      <ConfirmDialog
        open={!!open}
        onOpenChange={(o) => !o && setOpen(null)}
        tone="safety"
        title={open ? describePending(open) : ''}
        description={open && isMine(open) ? 'You requested this change; confirming it is your second, separate decision.' : 'Another operator requested this change.'}
        consequence="It applies now and loosens a safety limit, so CloudSentry can do more on its own. The audit log records who requested and who confirmed it."
        confirmLabel="Confirm change"
        pending={confirm.isPending}
        onConfirm={() =>
          open &&
          confirm.mutate(open, {
            onSuccess: () => { toast.success('Change applied', describePending(open)); setOpen(null) },
            onError: (e) => { toast.error('Could not confirm', e.message); setOpen(null) },
          })
        }
      />
    </Card>
  )
}
