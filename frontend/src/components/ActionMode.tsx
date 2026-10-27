import { useDashboardSummary } from '../hooks/queries'
import type { Action } from '../lib/types'
import { Badge } from './ui'

const OPEN = new Set(['pending', 'pending_approval', 'approved'])
const NEVER_RAN = new Set(['rejected', 'expired'])

// How an action runs (or ran). Finished rows carry the truthful recorded mode;
// open ones follow DRY_RUN_MODE as it is now, because that is what the runner
// will apply when they execute. Rejected or expired ones never ran: no badge.
export function ActionModeBadge({ action }: { action: Action }) {
  const { data } = useDashboardSummary()
  if (action.action_type === 'recommend_review') return <Badge>Review only</Badge>
  if (NEVER_RAN.has(action.status ?? '')) return null
  const dry = OPEN.has(action.status ?? '') && data ? data.dry_run : !!action.dry_run
  return dry ? <Badge tone="rehearsal">Dry run</Badge> : <Badge tone="critical">Live</Badge>
}
