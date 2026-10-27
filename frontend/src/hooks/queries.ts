import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '../lib/api'
import type {
  Action,
  ActionStatus,
  Anomaly,
  AnomalyStatus,
  AnomalySummary,
  AuditLog,
  ConfigChangeResult,
  CostPoint,
  DashboardSummary,
  EmergencyStopResult,
  PendingChange,
  MetricPoint,
  Resource,
  ResourceBundle,
  RuntimeConfig,
  SystemHealth,
} from '../lib/types'

const POLL_MS = 30_000
// Telemetry charts refresh faster so live samples show up while you watch.
const LIVE_MS = 10_000

// Health/detail endpoints return 503 bodies we still want to show, so callers
// render `error` alongside any stale `data`.

export const useDashboardSummary = () =>
  useQuery({
    queryKey: ['dashboard', 'summary'],
    queryFn: ({ signal }) => api<DashboardSummary>('/dashboard/summary', { signal }),
    refetchInterval: POLL_MS,
  })

export const useCostTrend = (days: number) =>
  useQuery({
    queryKey: ['dashboard', 'cost-trend', days],
    queryFn: ({ signal }) => api<CostPoint[]>('/dashboard/cost-trend', { query: { days }, signal }),
  })

export const useAnomalySummary = () =>
  useQuery({
    queryKey: ['dashboard', 'anomaly-summary'],
    queryFn: ({ signal }) => api<AnomalySummary>('/dashboard/anomaly-summary', { signal }),
    refetchInterval: POLL_MS,
  })

export const useSystemHealth = () =>
  useQuery({
    queryKey: ['system', 'health'],
    queryFn: ({ signal }) => api<SystemHealth>('/system/health', { signal }),
    refetchInterval: POLL_MS,
  })

export const useSystemConfig = () =>
  useQuery({
    queryKey: ['system', 'config'],
    queryFn: ({ signal }) => api<RuntimeConfig>('/system/config', { signal }),
  })

export const useResources = (params: { resource_type?: string; include_deleted?: boolean }) =>
  useQuery({
    queryKey: ['resources', params],
    queryFn: ({ signal }) => api<Resource[]>('/resources/', { query: { ...params, limit: 1000 }, signal }),
  })

export const useResource = (id: string) =>
  useQuery({
    queryKey: ['resources', 'detail', id],
    queryFn: ({ signal }) => api<ResourceBundle>(`/resources/${id}`, { signal }),
    refetchInterval: POLL_MS,
  })

export const useMetrics = (id: string, days: number, metric?: string) =>
  useQuery({
    queryKey: ['metrics', id, days, metric ?? null],
    queryFn: ({ signal }) => api<MetricPoint[]>(`/metrics/${id}`, { query: { days, metric }, signal }),
    refetchInterval: LIVE_MS,
  })

export const useAnomalies = (params: { status?: AnomalyStatus; resource_id?: string; limit?: number }) =>
  useQuery({
    queryKey: ['anomalies', params],
    queryFn: ({ signal }) => api<Anomaly[]>('/anomalies/', { query: { limit: 200, ...params }, signal }),
    refetchInterval: POLL_MS,
  })

export const useAnomaly = (id: string | null | undefined) =>
  useQuery({
    queryKey: ['anomalies', 'one', id],
    queryFn: ({ signal }) => api<Anomaly>(`/anomalies/${id}`, { signal }),
    enabled: !!id,
  })

export const useActions = (params: { status?: ActionStatus; limit?: number }) =>
  useQuery({
    queryKey: ['actions', params],
    queryFn: ({ signal }) => api<Action[]>('/actions/', { query: { limit: 200, ...params }, signal }),
    refetchInterval: POLL_MS,
  })

export interface AuditFilters {
  event_type?: string
  actor?: string
  request_id?: string
  resource_id?: string
  action_id?: string
  since?: string
  until?: string
}

export const useAuditLogs = (filters: AuditFilters, offset: number, limit = 50) =>
  useQuery({
    queryKey: ['audit', filters, offset, limit],
    queryFn: ({ signal }) => api<AuditLog[]>('/audit-logs/', { query: { ...filters, offset, limit }, signal }),
    placeholderData: (prev) => prev,
  })

export const usePendingChanges = () =>
  useQuery({
    queryKey: ['system', 'pending'],
    queryFn: ({ signal }) => api<PendingChange[]>('/system/config/pending', { signal }),
    refetchInterval: POLL_MS,
  })

// ---- mutations -------------------------------------------------------------

function useInvalidate() {
  const qc = useQueryClient()
  return (...keys: string[]) => Promise.all(keys.map((k) => qc.invalidateQueries({ queryKey: [k] })))
}

export function useEmergencyStop() {
  const invalidate = useInvalidate()
  return useMutation({
    mutationFn: () => api<EmergencyStopResult>('/system/emergency-stop', { method: 'POST' }),
    onSettled: () => invalidate('system', 'dashboard'),
  })
}

export function useUpdateConfig() {
  const invalidate = useInvalidate()
  return useMutation({
    mutationFn: (v: { key: string; value: string }) =>
      api<ConfigChangeResult>('/system/config', { method: 'PATCH', body: v }),
    onSettled: () => invalidate('system', 'dashboard'),
  })
}

// Confirming a pending change is repeating it; the server decides whether
// this caller may confirm yet (another operator, or the requester after the wait).
export function useConfirmPending() {
  const invalidate = useInvalidate()
  return useMutation({
    mutationFn: (p: PendingChange): Promise<unknown> => {
      const [kind, id, field] = p.key.split(':')
      if (kind === 'POLICY') return api(`/policies/${id}`, { method: 'PATCH', body: { [field]: p.value } })
      if (kind === 'RESOURCE') return api(`/resources/${id}/protection`, { method: 'PATCH', body: { protected: p.value } })
      return api<ConfigChangeResult>('/system/config', { method: 'PATCH', body: { key: p.key, value: String(p.value) } })
    },
    onSettled: () => invalidate('system', 'dashboard', 'resources', 'audit'),
  })
}

export function useCancelPending() {
  const invalidate = useInvalidate()
  return useMutation({
    mutationFn: (key: string) => api(`/system/config/pending/${encodeURIComponent(key)}/cancel`, { method: 'POST' }),
    onSettled: () => invalidate('system', 'dashboard', 'audit'),
  })
}

export function useDiscover() {
  const invalidate = useInvalidate()
  return useMutation({
    mutationFn: () => api<{ status: string; result: unknown }>('/resources/discover', { method: 'POST' }),
    onSettled: () => invalidate('resources', 'dashboard'),
  })
}

export function useSetAnomalyStatus() {
  const invalidate = useInvalidate()
  return useMutation({
    mutationFn: (v: { id: string; status: AnomalyStatus }) =>
      api<Anomaly>(`/anomalies/${v.id}/status`, { method: 'PATCH', body: { status: v.status } }),
    onSettled: () => invalidate('anomalies', 'dashboard', 'resources'),
  })
}

export type ActionOp = 'approve' | 'reject' | 'execute' | 'rollback'

export function useActionOp() {
  const invalidate = useInvalidate()
  return useMutation({
    mutationFn: ({ id, op }: { id: string; op: ActionOp }): Promise<unknown> => {
      if (op === 'approve' || op === 'reject') {
        return api<Action>(`/actions/${id}/approve`, { method: 'POST', body: { approved: op === 'approve' } })
      }
      return api<{ status: string; id: string }>(`/actions/${id}/${op}`, { method: 'POST' })
    },
    onSettled: () => invalidate('actions', 'dashboard', 'resources', 'audit'),
  })
}
