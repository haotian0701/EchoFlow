import { describe, expect, it } from 'vitest'
import { activeJob, jobStage, nextAction, reviewLabels, reviewState } from '../src/domain/review'
import type { Approval, EvaluationSummary, ProductionJob } from '../src/api/types'

const evaluation = (status: EvaluationSummary['status'], id = 'latest') => ({ id, status }) as EvaluationSummary
const approval = (id: string) => ({ evaluation_id: id }) as Approval
const job = (status: ProductionJob['status']) => ({ status, stage: 'provider_call' }) as ProductionJob

describe('current version review and next actions', () => {
  it.each([
    [null, [], null, 'pending_evaluation', 'Start evaluation'],
    [evaluation('queued'), [], job('queued'), 'evaluating', 'View evaluation progress'],
    [evaluation('running'), [], job('retry_scheduled'), 'evaluating', 'View evaluation progress'],
    [evaluation('failed'), [approval('older')], job('failed'), 'evaluation_failed', 'View failure details'],
    [evaluation('needs_attention'), [], job('needs_attention'), 'needs_attention', 'View failure details'],
    [evaluation('succeeded'), [approval('older')], job('succeeded'), 'pending_review', 'Review issues'],
    [evaluation('succeeded'), [approval('latest')], job('succeeded'), 'approved', 'Produce audio'],
  ] as const)('derives %s without inheriting historical approvals', (report, approvals, task, state, action) => {
    const actual = reviewState(true, report, [...approvals], task)
    expect(actual).toBe(state)
    expect(nextAction(actual)).toBe(action)
    expect(reviewLabels[actual]).toBeTruthy()
  })
  it('keeps an approved old version superseded', () => {
    expect(reviewState(false, evaluation('succeeded'), [approval('latest')])).toBe('superseded')
    expect(nextAction('superseded')).toBe('Return to latest version')
  })
  it('fails closed when the approval snapshot cannot be confirmed', () => {
    expect(reviewState(true, evaluation('succeeded'), [approval('latest')], null, true)).toBe('unavailable')
    expect(nextAction('unavailable')).toBe('Refresh status')
  })
  it('shows retry backoff and real provider stages', () => {
    expect(jobStage(job('retry_scheduled'))).toBe('Waiting for automatic retry')
    expect(jobStage(job('running'))).toBe('Calling provider')
    expect(activeJob(job('needs_attention'))).toBe(false)
  })
})
