import type { Language, ArticleSummary, Article, ImportAttempt, Page, ArticleWriteResult, GenerationConfig, ProductionJob, JobEvent, ScriptSummary, ScriptVersion, JobCreateResult, EvaluationSummary, Evaluation, EvaluationCreateResult, Approval, Voice, AudioAsset, AudioCreateResult, AudioDetail, JobExecution, ProviderCall, Overview } from './types'

const BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'

interface ApiErrorBody {
  code: string
  message: string
  details: Record<string, unknown>
  request_id: string
}

export class ApiError extends Error {
  constructor(public status: number, public body: ApiErrorBody) {
    super(body.message)
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE_URL}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  })
  if (response.status === 204) return undefined as T
  const data: unknown = await response.json()
  if (!response.ok) {
    throw new ApiError(response.status, data as ApiErrorBody)
  }
  return data as T
}

export const api = {
  monitorJobs: (params: Record<string, string>) => request<Page<ProductionJob>>(`/jobs?${new URLSearchParams(params)}`),
  executions: (id: string, offset = 0) => request<Page<JobExecution>>(`/jobs/${id}/executions?limit=20&offset=${offset}`),
  calls: (id: string, offset = 0) => request<Page<ProviderCall>>(`/jobs/${id}/calls?limit=20&offset=${offset}`),
  eventsPage: (id: string, offset = 0) => request<Page<JobEvent>>(`/jobs/${id}/events?limit=20&offset=${offset}`),
  retryJob: (id: string, acknowledge: boolean, key: string) => request<{job: ProductionJob; retry_mode: string}>(`/jobs/${id}/retry`, {
    method: 'POST', headers: {'Idempotency-Key': key}, body: JSON.stringify({acknowledge_possible_charge: acknowledge}),
  }),
  metrics: (period: string) => {
    const query = period === 'all' ? 'all_time=true' : `from=${encodeURIComponent(new Date(Date.now() - Number(period)*86400000).toISOString())}`
    return request<Overview>(`/metrics/overview?${query}`)
  },
  voices: (language: Language, signal?: AbortSignal) => request<{ items: Voice[]; cached: boolean }>(`/tts/voices?language=${language}`, { signal }),
  audioList: (params: { scriptId?: string; q?: string; offset?: number } = {}, signal?: AbortSignal) => {
    const query = new URLSearchParams({ limit: '20', offset: String(params.offset ?? 0), q: params.q ?? '' })
    if (params.scriptId) query.set('script_id', params.scriptId)
    return request<Page<AudioAsset>>(`/audio?${query}`, { signal })
  },
  audio: (id: string, signal?: AbortSignal) => request<AudioDetail>(`/audio/${id}`, { signal }),
  createAudio: (scriptId: string, body: { language: Language; voice_id: string; speed: number }, key: string) =>
    request<AudioCreateResult>(`/scripts/${scriptId}/audio`, {
      method: 'POST', headers: { 'Idempotency-Key': key }, body: JSON.stringify(body),
    }),
  audioJobs: (scriptId: string, signal?: AbortSignal) => request<Page<ProductionJob>>(`/jobs?script_id=${scriptId}&type=audio_generation&limit=20&offset=0`, { signal }),
  audioStream: (id: string) => `${BASE_URL}/audio/${id}/stream`,
  audioDownload: (id: string) => `${BASE_URL}/audio/${id}/download`,
  deleteAudio: (id: string) => request<void>(`/audio/${id}`, { method: 'DELETE' }),
  articles: (offset: number, q: string, signal?: AbortSignal) =>
    request<Page<ArticleSummary>>(`/articles?limit=20&offset=${offset}&q=${encodeURIComponent(q)}`, { signal }),
  article: (id: string, signal?: AbortSignal) => request<Article>(`/articles/${id}`, { signal }),
  imports: (offset: number, signal?: AbortSignal) => request<Page<ImportAttempt>>(`/articles/imports?limit=20&offset=${offset}`, { signal }),
  create: (body: { title: string; text: string; source: string; language: Language }) =>
    request<ArticleWriteResult>('/articles', { method: 'POST', body: JSON.stringify(body) }),
  import: (body: { url: string; title?: string; source?: string; language: Language }) =>
    request<ArticleWriteResult>('/articles/import', { method: 'POST', body: JSON.stringify(body) }),
  createScriptJob: (articleId: string, config: GenerationConfig, idempotencyKey: string) =>
    request<JobCreateResult>('/jobs', {
      method: 'POST',
      headers: { 'Idempotency-Key': idempotencyKey },
      body: JSON.stringify({ type: 'script_generation', article_id: articleId, config }),
    }),
  jobs: (articleId: string, signal?: AbortSignal) =>
    request<Page<ProductionJob>>(`/jobs?article_id=${encodeURIComponent(articleId)}&type=script_generation&limit=20&offset=0`, { signal }),
  job: (id: string, signal?: AbortSignal) => request<ProductionJob>(`/jobs/${id}`, { signal }),
  jobEvents: (id: string, signal?: AbortSignal) => request<{ items: JobEvent[] }>(`/jobs/${id}/events`, { signal }),
  scripts: (articleId: string, signal?: AbortSignal) => request<Page<ScriptSummary>>(`/articles/${articleId}/scripts?limit=100&offset=0`, { signal }),
  script: (id: string, signal?: AbortSignal) => request<ScriptVersion>(`/scripts/${id}`, { signal }),
  regenerate: (scriptId: string, config: GenerationConfig, idempotencyKey: string) =>
    request<JobCreateResult>(`/scripts/${scriptId}/regenerate`, {
      method: 'POST',
      headers: { 'Idempotency-Key': idempotencyKey },
      body: JSON.stringify({ config }),
    }),
  evaluations: (scriptId: string, limit = 20, signal?: AbortSignal) =>
    request<Page<EvaluationSummary>>(`/scripts/${scriptId}/evaluations?limit=${limit}&offset=0`, { signal }),
  evaluation: (id: string, signal?: AbortSignal) => request<Evaluation>(`/evaluations/${id}`, { signal }),
  evaluate: (scriptId: string, idempotencyKey: string) =>
    request<EvaluationCreateResult>(`/scripts/${scriptId}/evaluate`, {
      method: 'POST',
      headers: { 'Idempotency-Key': idempotencyKey },
    }),
  editScript: (scriptId: string, text: string, expectedVersion: number) =>
    request<ScriptVersion>(`/scripts/${scriptId}`, {
      method: 'PATCH',
      body: JSON.stringify({ text, expected_version: expectedVersion }),
    }),
  approvals: (scriptId: string, signal?: AbortSignal) =>
    request<{ items: Approval[] }>(`/scripts/${scriptId}/approvals`, { signal }),
  approve: (
    scriptId: string,
    body: { evaluation_id: string; expected_version: number; acknowledged_critical_issue_ids: string[] },
  ) => request<Approval>(`/scripts/${scriptId}/approve`, { method: 'POST', body: JSON.stringify(body) }),
}



