// Dev-only sample data (VITE_DEMO=1 with `npm run dev`). Lets the UI be built
// and reviewed without a backend or Supabase account. Never bundled in
// production: api.ts only imports this file behind import.meta.env.DEV.
import { ApiError, type RequestOptions } from './api'
import type { Action, Anomaly, AuditLog, MetricPoint, PendingChange, Resource } from './types'

let seed = 42
const rand = () => ((seed = (seed * 16807) % 2147483647) - 1) / 2147483646
const pick = <T,>(xs: readonly T[]) => xs[Math.floor(rand() * xs.length)]
const uuid = () =>
  'xxxxxxxx-xxxx-4xxx-8xxx-xxxxxxxxxxxx'.replace(/x/g, () => Math.floor(rand() * 16).toString(16))

const NOW = Date.now()
const HOUR = 3600_000
const DAY = 24 * HOUR
const iso = (t: number) => new Date(t).toISOString()

const REGIONS = ['us-east-1', 'us-east-1', 'us-west-2', 'eu-west-1']
const NAMES: Record<string, string[]> = {
  ec2: ['api-gateway-1', 'api-gateway-2', 'batch-worker', 'bastion', 'ml-trainer', 'web-frontend', 'reporting-etl', 'ci-runner'],
  lambda: ['thumbnailer', 'invoice-webhook', 'nightly-export', 'auth-trigger', 'slack-notifier', 'log-shipper'],
  s3: ['assets-prod', 'backups-archive', 'data-lake-raw'],
  rds: ['orders-db', 'analytics-replica'],
  ebs: ['vol-orphan-build', 'vol-scratch', 'vol-ml-cache'],
}

const resources: Resource[] = Object.entries(NAMES).flatMap(([type, names]) =>
  names.map((name) => {
    const state = type === 'ec2' ? pick(['running', 'running', 'running', 'stopped']) : type === 'ebs' ? pick(['available', 'in-use']) : 'active'
    return {
      id: uuid(),
      provider_id: type === 'ec2' ? `i-0${Math.floor(rand() * 1e15).toString(16)}` : type === 'ebs' ? `vol-0${Math.floor(rand() * 1e15).toString(16)}` : name,
      resource_type: type,
      name,
      region: pick(REGIONS),
      state,
      tags: { env: pick(['prod', 'prod', 'staging', 'dev']), team: pick(['platform', 'data', 'web']), ...(name === 'bastion' ? { 'cloudsentry:protected': 'true' } : {}) },
      protected: name === 'bastion' || type === 'rds',
      metadata: { instance_type: type === 'ec2' ? pick(['t3.medium', 'm5.large', 'c6i.xlarge']) : undefined },
      account_id: null,
      first_seen: iso(NOW - (20 + rand() * 60) * DAY),
      last_seen: iso(NOW - rand() * 14 * 60_000),
    }
  }),
)

const ANOMALY_TYPES = ['idle_instance', 'cpu_spike', 'cost_spike', 'untagged_resource', 'invocation_surge', 'unattached_volume']
const REASONS: Record<string, string> = {
  idle_instance: 'CPU below 3% for 72 h while running',
  cpu_spike: 'CPU 4.2σ above its 7-day baseline',
  cost_spike: 'Daily cost 2.8× the trailing average',
  untagged_resource: 'Missing required tags: owner, cost-center',
  invocation_surge: 'Invocations 6× the hourly baseline',
  unattached_volume: 'Volume unattached for 9 days',
}

const anomalies: Anomaly[] = Array.from({ length: 46 }, (_, i) => {
  const r = pick(resources.filter((x) => x.resource_type !== 's3'))
  const t = pick(ANOMALY_TYPES)
  const detected = NOW - Math.pow(rand(), 1.6) * 30 * DAY
  const status: Anomaly['status'] = i < 9 ? 'active' : pick(['resolved', 'resolved', 'resolved', 'false_positive'] as const)
  return {
    id: uuid(),
    resource_id: r.id,
    anomaly_type: t,
    severity: pick(['HIGH', 'MEDIUM', 'MEDIUM', 'LOW', 'LOW'] as const),
    anomaly_score: Math.round(rand() * 100) / 100,
    confidence: Math.round((0.6 + rand() * 0.4) * 100) / 100,
    reason: REASONS[t],
    features_snapshot: { cpu_mean_1h: Math.round(rand() * 900) / 10, cpu_std_24h: Math.round(rand() * 200) / 10, network_in_mb: Math.round(rand() * 5000) },
    model_version: pick(['rules', 'zscore', 'if_ec2_20261003']),
    status,
    detected_at: iso(i < 9 ? NOW - rand() * 3 * DAY : detected),
    resolved_at: status === 'active' ? null : iso(detected + rand() * DAY),
  }
}).sort((a, b) => (b.detected_at! > a.detected_at! ? 1 : -1))

const ACTION_TYPES = ['stop_ec2', 'stop_ec2', 'limit_lambda', 'apply_tags', 'recommend_review']
const actions: Action[] = Array.from({ length: 28 }, (_, i) => {
  const a = anomalies[i % anomalies.length]
  const type = pick(ACTION_TYPES)
  const status = i < 4 ? 'pending_approval' : i === 4 ? 'approved' : i === 5 ? 'executing' : pick(['completed', 'completed', 'completed', 'rejected', 'failed', 'rolled_back'] as const)
  const created = NOW - (i < 6 ? rand() * 2 * DAY : (2 + rand() * 25) * DAY)
  return {
    id: uuid(),
    anomaly_id: a.id,
    resource_id: a.resource_id,
    action_type: type,
    risk_level: type === 'stop_ec2' ? 'medium' : type === 'apply_tags' ? 'low' : pick(['low', 'medium']),
    status,
    dry_run: i % 3 !== 0,
    requires_approval: type === 'stop_ec2',
    pre_state: { state: 'running', instance_type: 'm5.large' },
    post_state: status === 'completed' ? { state: 'stopped' } : null,
    estimated_savings_usd: type === 'apply_tags' || type === 'recommend_review' ? 0 : Math.round((8 + rand() * 140) * 100) / 100,
    approved_by: ['approved', 'executing', 'completed', 'failed', 'rolled_back'].includes(status) ? pick(['priya@ops', 'sam@ops']) : null,
    approved_at: status === 'pending_approval' ? null : iso(created + HOUR),
    created_at: iso(created),
    claimed_at: null,
    executed_at: ['completed', 'failed', 'rolled_back'].includes(status) ? iso(created + 2 * HOUR) : null,
    verified_at: status === 'completed' ? iso(created + 2.2 * HOUR) : null,
    rollback_action_id: status === 'rolled_back' ? uuid() : null,
  }
})

const costTrend = Array.from({ length: 90 }, (_, i) => {
  const day = new Date(NOW - (89 - i) * DAY)
  const weekday = day.getUTCDay()
  const base = 41 + i * 0.09 + (weekday === 0 || weekday === 6 ? -6 : 0)
  const spike = i === 71 || i === 72 ? 19 : 0
  return { date: day.toISOString().slice(0, 10), cost_usd: Math.round((base + spike + (rand() - 0.5) * 5) * 100) / 100 }
})

const EVENTS = ['config_changed', 'action_approved', 'action_executed', 'anomaly_status_changed', 'discovery_completed', 'emergency_stop', 'config_change_requested']
const audit: AuditLog[] = Array.from({ length: 160 }, () => {
  const ev = pick(EVENTS)
  return {
    id: uuid(),
    event_type: ev,
    actor: pick(['priya@ops', 'sam@ops', 'SYSTEM', 'SYSTEM']),
    resource_id: rand() > 0.4 ? pick(resources).id : null,
    action_id: ev.startsWith('action') ? pick(actions).id : null,
    aws_api_call: ev === 'action_executed' ? 'ec2:StopInstances' : null,
    request_params: ev.startsWith('config') ? { key: 'MAX_ACTIONS_PER_DAY', value: '8' } : { dry_run: true },
    response_status: pick(['success', 'success', 'success', 'error']),
    message: { config_changed: 'MAX_ACTIONS_PER_DAY set to 8', action_approved: 'Approved stop_ec2', action_executed: 'WOULD stop instance (dry run)', anomaly_status_changed: 'Anomaly set to resolved', discovery_completed: 'Discovered 22 resources', emergency_stop: 'GLOBAL_AUTOMATION_ENABLED set to false via emergency stop', config_change_requested: 'DRY_RUN_MODE=false requested; awaiting a second operator' }[ev] ?? ev,
    request_id: Math.floor(rand() * 1e12).toString(36),
    client_ip: '10.0.4.' + Math.floor(rand() * 250),
    created_at: iso(NOW - Math.pow(rand(), 1.3) * 30 * DAY),
  }
}).sort((a, b) => (b.created_at > a.created_at ? 1 : -1))

const config: Record<string, boolean | number> = {
  GLOBAL_AUTOMATION_ENABLED: false,
  DRY_RUN_MODE: true,
  MAX_ACTIONS_PER_DAY: 5,
  ACTION_COOLDOWN_MINUTES: 30,
  MAX_CW_API_CALLS_PER_HOUR: 1000,
  MAX_DAILY_SPEND_USD: 2,
  MAX_MONTHLY_BUDGET_USD: 10,
}

// The demo runs in solo mode (TWO_PERSON_MODE=solo_delay) and starts with one
// request per state: still waiting, confirmable now, and expired.
const MIN = 60_000
const SOLO_DELAY = 15 * MIN
const ME = 'USER:demo'
const pendingChange = (key: string, value: PendingChange['value'], by: string, ageMin: number): PendingChange => {
  const at = NOW - ageMin * MIN
  return { key, value, requested_by: by, requested_at: iso(at), expires_at: iso(at + HOUR), expired: false, confirmable_at: iso(at + SOLO_DELAY) }
}
let pending: PendingChange[] = [
  pendingChange('DRY_RUN_MODE', 'false', ME, 4),
  pendingChange('MAX_ACTIONS_PER_DAY', '20', ME, 22),
  pendingChange(`RESOURCE:${resources[0].id}:protected`, false, 'USER:ops-2', 75),
]

function metrics(id: string, days: number, metric?: string): MetricPoint[] {
  const r = resources.find((x) => x.id === id)
  const names = r?.resource_type === 'lambda' ? ['Invocations', 'Duration', 'Errors'] : ['CPUUtilization', 'NetworkIn', 'NetworkOut']
  const out: MetricPoint[] = []
  const step = days > 1 ? HOUR : 5 * 60_000
  for (const name of metric ? [metric] : names) {
    for (let t = NOW - days * DAY; t <= NOW; t += step) {
      const hour = new Date(t).getHours()
      const daily = Math.sin(((hour - 6) / 24) * Math.PI * 2) * 0.5 + 0.5
      const spike = NOW - t < 6 * HOUR && NOW - t > 4 * HOUR ? 2.4 : 1
      const base = name === 'CPUUtilization' ? 18 + daily * 30 : name === 'Errors' ? daily * 2 : name === 'Duration' ? 120 + daily * 80 : 400 + daily * 900
      out.push({ time: iso(t), metric_name: name, value: Math.max(0, Math.round(base * spike * (0.85 + rand() * 0.3) * 10) / 10), unit: name === 'CPUUtilization' ? 'Percent' : name === 'Duration' ? 'Milliseconds' : 'Count' })
    }
  }
  return out.reverse()
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

// Mirrors runtime_config.check_confirmer: repeating a change confirms its live
// request (the requester only after the wait); otherwise it files a new one.
function confirmPending(key: string, value: PendingChange['value']): 'requested' | 'confirmed' {
  const now = Date.now()
  const p = pending.find((x) => x.key === key && now <= Date.parse(x.expires_at))
  if (!p) {
    pending = [pendingChange(key, value, ME, 0), ...pending.filter((x) => x.key !== key)]
    return 'requested'
  }
  if (p.requested_by === ME && p.confirmable_at && now < Date.parse(p.confirmable_at)) {
    throw new ApiError(409, `You can confirm your own request from ${p.confirmable_at.slice(11, 16)} UTC, or a different operator can confirm it now`, null)
  }
  pending = pending.filter((x) => x !== p)
  return 'confirmed'
}

export async function demoApi<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  await sleep(120 + rand() * 180)
  const q = opts.query ?? {}
  const method = opts.method ?? 'GET'
  const body = (opts.body ?? {}) as Record<string, string>
  const r = <X,>(x: X) => x as unknown as T

  if (path === '/dashboard/summary') {
    return r({
      active_resources: resources.filter((x) => x.state !== 'terminated').length,
      active_anomalies: anomalies.filter((a) => a.status === 'active').length,
      potential_savings_usd: actions.filter((a) => ['pending', 'pending_approval', 'approved'].includes(a.status!)).reduce((s, a) => s + (a.estimated_savings_usd ?? 0), 0),
      realized_savings_usd: actions.filter((a) => a.status === 'completed' && !a.dry_run).reduce((s, a) => s + (a.estimated_savings_usd ?? 0), 0),
      month_to_date_cost_estimated_usd: costTrend.slice(-new Date().getDate()).reduce((s, d) => s + d.cost_usd, 0),
      automation_enabled: config.GLOBAL_AUTOMATION_ENABLED,
      dry_run: config.DRY_RUN_MODE,
    })
  }
  if (path === '/dashboard/cost-trend') return r(costTrend.slice(-Number(q.days ?? 7)))
  if (path === '/dashboard/anomaly-summary') {
    const act = anomalies.filter((a) => a.status === 'active')
    const c = { HIGH: 0, MEDIUM: 0, LOW: 0 }
    act.forEach((a) => (c[a.severity ?? 'LOW'] += 1))
    return r({ ...c, total: act.length })
  }
  if (path === '/system/health') {
    return r({
      status: 'degraded',
      db: { status: 'ok', latency_ms: 18.4 },
      ml_model: { status: 'ok', models: ['ec2', 'lambda'], mode: 'isolation_forest', details: { ec2: { version: 'if_ec2_20261003', trained_at: iso(NOW - 9 * HOUR), n_samples: 48210 }, lambda: { version: 'if_lambda_20261003', trained_at: iso(NOW - 9 * HOUR), n_samples: 22904 } } },
      telemetry: { last_collection: iso(NOW - 3 * 60_000), staleness_minutes: 3 },
      automation: { enabled: config.GLOBAL_AUTOMATION_ENABLED, dry_run: config.DRY_RUN_MODE, two_person: 'solo_delay' },
      scheduler: { running: true, leader: true },
      jobs: {
        discovery: { last_success: iso(NOW - 6 * 60_000), last_failure: null, consecutive_failures: 0 },
        pipeline: { last_success: iso(NOW - 2 * 60_000), last_failure: iso(NOW - 3 * DAY), consecutive_failures: 0 },
        execute: { last_success: iso(NOW - 60_000), consecutive_failures: 0 },
        verify: { last_success: iso(NOW - 60_000), consecutive_failures: 0 },
        cost: { last_success: iso(NOW - 40 * 60_000), last_failure: iso(NOW - 10 * 60_000), last_error: 'Cost Explorer throttled', consecutive_failures: 1 },
        retrain: { last_success: iso(NOW - 9 * HOUR), consecutive_failures: 0 },
        retention: { last_success: iso(NOW - 14 * HOUR), consecutive_failures: 0 },
      },
      budget: { over_budget: false, reason: null },
      issues: ['job_failing:cost'],
    })
  }
  if (path === '/system/config' && method === 'GET') return r({ ...config })
  if (path === '/system/config/pending') {
    const now = Date.now()
    return r(pending.map((p) => ({ ...p, expired: now > Date.parse(p.expires_at) })))
  }
  const cancel = path.match(/^\/system\/config\/pending\/(.+)\/cancel$/)
  if (cancel) {
    const key = decodeURIComponent(cancel[1])
    pending = pending.filter((p) => p.key !== key)
    return r({ status: 'cancelled', key })
  }
  if (path === '/system/config' && method === 'PATCH') {
    const unsafe = (body.key === 'GLOBAL_AUTOMATION_ENABLED' && body.value === 'true') || (body.key === 'DRY_RUN_MODE' && body.value === 'false')
      || (body.key.startsWith('MAX_') && Number(body.value) > Number(config[body.key]))
    if (unsafe && confirmPending(body.key, body.value) === 'requested') {
      const p = pending[0]
      return r({ status: 'pending_confirmation', key: body.key, value: body.value, expires_at: p.expires_at, confirmable_at: p.confirmable_at })
    }
    config[body.key] = body.value === 'true' ? true : body.value === 'false' ? false : Number(body.value)
    return r({ status: 'success', key: body.key, value: config[body.key] })
  }
  const loosen = path.match(/^\/(?:policies\/([\w-]+)|resources\/([\w-]+)\/protection)$/)
  if (loosen && method === 'PATCH') {
    const [field, value] = Object.entries(opts.body as Record<string, boolean>)[0]
    confirmPending(loosen[1] ? `POLICY:${loosen[1]}:${field}` : `RESOURCE:${loosen[2]}:protected`, value)
    return r({ status: 'success' })
  }
  if (path === '/system/emergency-stop') {
    config.GLOBAL_AUTOMATION_ENABLED = false
    return r({ automation_enabled: false, persisted: true, audit_logged: true, config })
  }
  if (path === '/resources/discover') return r({ status: 'success', result: { discovered: resources.length } })
  if (path === '/resources/') {
    return r(resources.filter((x) => !q.resource_type || x.resource_type === q.resource_type))
  }
  let m = path.match(/^\/resources\/([\w-]+)$/)
  if (m) {
    const res = resources.find((x) => x.id === m![1])
    return r({
      resource: res,
      anomalies: anomalies.filter((a) => a.resource_id === res?.id),
      actions: actions.filter((a) => a.resource_id === res?.id),
      metrics_24h: metrics(m[1], 1).slice(0, 60),
    })
  }
  m = path.match(/^\/metrics\/([\w-]+)$/)
  if (m) return r(metrics(m[1], Number(q.days ?? 1), q.metric as string | undefined))
  if (path === '/anomalies/') {
    return r(anomalies.filter((a) => (!q.status || a.status === q.status) && (!q.resource_id || a.resource_id === q.resource_id)).slice(0, Number(q.limit ?? 100)))
  }
  m = path.match(/^\/anomalies\/([\w-]+)$/)
  if (m) return r(anomalies.find((x) => x.id === m![1]))
  m = path.match(/^\/anomalies\/([\w-]+)\/status$/)
  if (m) {
    const a = anomalies.find((x) => x.id === m![1])!
    a.status = body.status as Anomaly['status']
    return r(a)
  }
  if (path === '/actions/') return r(actions.filter((a) => !q.status || a.status === q.status).slice(0, Number(q.limit ?? 100)))
  m = path.match(/^\/actions\/([\w-]+)\/(approve|execute|rollback)$/)
  if (m) {
    const a = actions.find((x) => x.id === m![1])!
    if (m[2] === 'approve') a.status = (opts.body as { approved: boolean }).approved ? 'approved' : 'rejected'
    if (m[2] === 'execute') a.status = 'executing'
    if (m[2] === 'rollback') a.status = 'rolled_back'
    return r(m[2] === 'approve' ? a : { status: 'queued', id: a.id })
  }
  if (path === '/audit-logs/') {
    const rows = audit.filter((l) =>
      (!q.event_type || l.event_type === q.event_type) &&
      (!q.actor || l.actor === q.actor) &&
      (!q.since || l.created_at >= String(q.since)) &&
      (!q.until || l.created_at < String(q.until)))
    const off = Number(q.offset ?? 0)
    return r(rows.slice(off, off + Number(q.limit ?? 50)))
  }
  throw new Error(`demo: no fixture for ${method} ${path}`)
}

export const demoUser = { email: 'demo@cloudsentry.local', role: 'operator' }
