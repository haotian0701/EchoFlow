import { api } from './client'

export async function readReview(scriptId: string, signal?: AbortSignal) {
  const [history, approvals] = await Promise.all([api.evaluations(scriptId, 100, signal), api.approvals(scriptId, signal)])
  const latest = history.items[0]
  const [evaluation, job] = latest ? await Promise.all([api.evaluation(latest.id, signal), api.job(latest.job_id, signal)]) : [null, null]
  return { history: history.items, approvals: approvals.items, evaluation, job }
}
