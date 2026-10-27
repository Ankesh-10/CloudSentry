import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

vi.mock('../lib/supabase', () => ({ supabase: null, getAccessToken: vi.fn(async () => null) }))

import { ConfirmButton, ErrorBox } from '../components/ui'
import { ApiError } from '../lib/api'
import type { Action } from '../lib/types'
import { availableOps } from '../pages/Actions'

const base: Action = {
  id: 'a1', anomaly_id: null, resource_id: null, action_type: 'stop_ec2', risk_level: 'low',
  status: 'pending', dry_run: true, requires_approval: true, pre_state: null, post_state: null,
  estimated_savings_usd: 1, approved_by: null, approved_at: null, created_at: null, claimed_at: null,
  executed_at: null, verified_at: null, rollback_action_id: null,
}

describe('availableOps mirrors backend guards', () => {
  it('pending + requires approval: approve/reject only', () => {
    expect(availableOps(base)).toEqual(['approve', 'reject'])
  })
  it('pending without approval requirement can also execute', () => {
    expect(availableOps({ ...base, requires_approval: false })).toEqual(['approve', 'reject', 'execute'])
  })
  it('approved: execute', () => {
    expect(availableOps({ ...base, status: 'approved' })).toEqual(['execute'])
  })
  it('completed reversible: rollback; not twice; not for recommend_review', () => {
    expect(availableOps({ ...base, status: 'completed' })).toEqual(['rollback'])
    expect(availableOps({ ...base, status: 'completed', rollback_action_id: 'r1' })).toEqual([])
    expect(availableOps({ ...base, status: 'completed', action_type: 'recommend_review' })).toEqual([])
  })
  it('terminal states: nothing', () => {
    for (const status of ['rejected', 'failed', 'rolled_back', 'executing'] as const) {
      expect(availableOps({ ...base, status })).toEqual([])
    }
  })
})

describe('ConfirmButton', () => {
  it('needs two clicks', async () => {
    const onConfirm = vi.fn()
    render(<ConfirmButton onConfirm={onConfirm} confirmLabel="Sure?">Execute</ConfirmButton>)
    await userEvent.click(screen.getByRole('button', { name: 'Execute' }))
    expect(onConfirm).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: 'Sure?' }))
    expect(onConfirm).toHaveBeenCalledOnce()
  })
})

describe('ErrorBox', () => {
  it('shows message and request id', () => {
    render(<ErrorBox error={new ApiError(409, 'Action changed concurrently', 'req-7')} />)
    expect(screen.getByRole('alert')).toHaveTextContent('Action changed concurrently')
    expect(screen.getByRole('alert')).toHaveTextContent('req-7')
  })
})
