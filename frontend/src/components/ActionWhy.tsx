import { Link } from 'react-router-dom'

import { useAnomaly } from '../hooks/queries'
import { usd } from '../lib/format'
import type { Action } from '../lib/types'
import { Skeleton } from './ui'

// "Explain before act": the reason a proposal exists, taken from the anomaly
// that triggered it, shown wherever an operator is asked to decide.
export function ActionWhy({ action, resourceName }: { action: Action; resourceName?: string }) {
  const anomaly = useAnomaly(action.anomaly_id)
  const facts = [
    resourceName && `Resource ${resourceName}`,
    action.estimated_savings_usd ? `saves ${usd(action.estimated_savings_usd)}/mo` : null,
    action.risk_level && `${action.risk_level.toLowerCase()} risk`,
  ].filter(Boolean)

  return (
    <div className="rounded-lg border border-line px-4 py-3 text-[13px]">
      <div className="mb-0.5 font-medium text-fg">Why it was proposed</div>
      {!action.anomaly_id ? (
        <p className="text-fg-2">Proposed by a policy rule, not by an anomaly.</p>
      ) : anomaly.isLoading ? (
        <Skeleton className="mt-1 h-4" />
      ) : anomaly.data?.reason ? (
        <p className="text-fg-2">
          {anomaly.data.reason}{' '}
          <Link to={`/anomalies?id=${action.anomaly_id}`} className="whitespace-nowrap text-info hover:underline">View anomaly</Link>
        </p>
      ) : (
        <p className="text-fg-2">The triggering anomaly could not be loaded.</p>
      )}
      {!!facts.length && <p className="mt-1.5 text-[12.5px] text-muted">{facts.join(' · ')}</p>}
    </div>
  )
}
