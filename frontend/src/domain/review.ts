import type { Approval, EvaluationSummary, ProductionJob } from '../api/types'

export type ReviewState = 'unavailable' | 'superseded' | 'pending_evaluation' | 'evaluating' | 'evaluation_failed' | 'needs_attention' | 'pending_review' | 'approved'
export const activeJob = (job: ProductionJob | null | undefined) => !!job && ['queued', 'running', 'retry_scheduled'].includes(job.status)
export const reviewLabels: Record<ReviewState, string> = {
  unavailable: 'Status unavailable', superseded: 'Superseded', pending_evaluation: 'Pending evaluation', evaluating: 'Evaluating',
  evaluation_failed: 'Evaluation failed', needs_attention: 'Needs attention', pending_review: 'Pending review', approved: 'Approved',
}

export function reviewState(isLatest: boolean, evaluation: EvaluationSummary | null, approvals: Approval[], job: ProductionJob | null = null, unavailable = false): ReviewState {
  if (unavailable) return 'unavailable'
  if (!isLatest) return 'superseded'
  if (!evaluation) return 'pending_evaluation'
  if (activeJob(job) || ['queued', 'running'].includes(evaluation.status)) return 'evaluating'
  if (evaluation.status === 'failed') return 'evaluation_failed'
  if (evaluation.status === 'needs_attention') return 'needs_attention'
  return approvals.some(item => item.evaluation_id === evaluation.id) ? 'approved' : 'pending_review'
}

export function nextAction(state: ReviewState) {
  const actions: Record<ReviewState, string> = {
    unavailable: 'Refresh status', superseded: 'Return to latest version', pending_evaluation: 'Start evaluation', evaluating: 'View evaluation progress',
    evaluation_failed: 'View failure details', needs_attention: 'View failure details', pending_review: 'Review issues', approved: 'Produce audio',
  }
  return actions[state]
}

export function jobStage(job: ProductionJob) {
  if (job.status === 'retry_scheduled') return 'Waiting for automatic retry'
  if (job.status === 'queued') return 'Queued'
  if (job.status === 'succeeded') return 'Completed'
  if (job.status === 'failed') return 'Failed'
  if (job.status === 'needs_attention') return 'Needs attention'
  return ({ preparing: 'Preparing', provider_call: 'Calling provider', saving: 'Saving results', uploading: 'Uploading audio', resume_upload: 'Resuming saved audio upload' } as Record<string, string>)[job.stage] || job.stage.replaceAll('_', ' ')
}
