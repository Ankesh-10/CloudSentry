import type { ActionOp } from '../hooks/queries'
import type { Action, DashboardSummary } from './types'

// What an operation will really do, given the mode in force *now*. Mirrors the
// backend: the runner applies the current DRY_RUN_MODE at execution time, the
// safety layer blocks runs while the kill-switch is off (rollbacks excepted),
// and recommend_review never calls the cloud.
export interface Effect {
  live: boolean
  text: string
}

type Mode = Pick<DashboardSummary, 'automation_enabled' | 'dry_run'> | undefined

const NO_CLOUD = new Set(['recommend_review'])

export function effectOf(op: ActionOp, a: Action, mode: Mode): Effect {
  if (op === 'reject') {
    return { live: false, text: 'The proposal is closed and will not run. The policy engine may propose it again later.' }
  }
  if (a.action_type && NO_CLOUD.has(a.action_type)) {
    return { live: false, text: 'Recommendation only: this records the review. Nothing changes in the cloud.' }
  }
  if (op === 'rollback') {
    const wasDry = !!a.dry_run || !!(a.post_state as { dry_run?: boolean } | null)?.dry_run
    if (wasDry) return { live: false, text: 'The original ran as a dry run, so the rollback is logged the same way. Nothing changes in the cloud.' }
    if (mode?.dry_run) return { live: false, text: 'Dry-run mode is on: the rollback is logged as “would act”. Nothing changes in the cloud.' }
    return { live: true, text: 'Restores the resource to its recorded pre-state now (e.g. restarts a stopped instance). Rollbacks run even while automation is stopped.' }
  }
  const verb = op === 'approve' ? 'Approval runs it right away' : 'It runs right away'
  if (!mode) {
    return { live: true, text: `${verb}. If automation is on and live, it changes the real resource after every safety check.` }
  }
  if (!mode.automation_enabled) {
    return { live: false, text: `${verb}, but automation is off: the kill-switch blocks the run and the audit log records it. Nothing changes in the cloud.` }
  }
  if (mode.dry_run) {
    return { live: false, text: `${verb} in dry-run mode: it is logged as “would act”. Nothing changes in the cloud.` }
  }
  return { live: true, text: `${verb} against the real resource, after protection tags, caps, cooldown and budget are checked.` }
}
