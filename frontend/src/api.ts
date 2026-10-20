const BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'

export type Language = 'en' | 'de'

export interface ArticleSummary {
  id: string
  title: string
  source: string
  source_url: string | null
  language: Language
  import_type: 'manual' | 'url'
  created_at: string
}

export interface Article extends ArticleSummary {
  original_text: string
  content_hash: string
}

export interface ImportAttempt {
  id: string
  url: string
  status: 'succeeded' | 'duplicate' | 'failed'
  article_id: string | null
  error_code: string | null
  error_message: string | null
  created_at: string
}

export interface Page<T> {
  items: T[]
  total: number
  limit: number
  offset: number
}

export interface ArticleWriteResult {
  article: Article
  duplicate: boolean
  import_attempt: ImportAttempt | null
}

export type ScriptLength = 'short' | 'medium' | 'long'
export type ScriptStyle = 'news' | 'conversational' | 'professional'
export type ScriptAudience = 'general' | 'business'
export type JobStatus = 'queued' | 'running' | 'retry_scheduled' | 'succeeded' | 'failed' | 'needs_attention'

export interface GenerationConfig {
  target_language: Language
  length: ScriptLength
  style: ScriptStyle
  audience: ScriptAudience
}

export interface ProductionJob {
  article_title: string
  retry_of_job_id: string | null
  root_job_id: string | null
  current_execution_id: string | null
  provider_snapshot: Record<string, unknown>
  can_retry: boolean
  retry_mode: 'new_call' | 'resume_upload'
  requires_charge_acknowledgement: boolean
  retry_child_job_id: string | null
  retry_unavailable_reason: string | null
  recovery_expires_at: string | null
  id: string
  job_type: 'script_generation' | 'script_evaluation' | 'audio_generation'
  article_id: string
  source_script_version_id: string | null
  result_script_version_id: string | null
  result_evaluation_id: string | null
  result_audio_asset_id: string | null
  status: JobStatus
  stage: string
  config: GenerationConfig | Record<string, unknown>
  attempt_count: number
  max_attempts: number
  error_code: string | null
  error_message: string | null
  created_at: string
  updated_at: string
  started_at: string | null
  completed_at: string | null
}

export interface JobEvent {
  id: string
  job_id: string
  event_type: string
  stage: string
  message: string
  metadata: Record<string, unknown>
  created_at: string
}

export interface ScriptSummary {
  id: string
  article_id: string
  version: number
  source: 'generated' | 'edited'
  parent_version_id: string | null
  target_language: Language
  generation_config: GenerationConfig
  provider: string
  requested_model: string
  actual_model: string
  prompt_version: string
  estimated_duration_seconds: number
  created_at: string
}

export interface ScriptVersion extends ScriptSummary {
  text: string
}

export interface JobCreateResult {
  job: ProductionJob
  idempotent_replay: boolean
}

export type EvaluationStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'needs_attention'
export type EvaluationDimension = 'factual_consistency' | 'content_coverage' | 'audio_readiness'
export type IssueSeverity = 'info' | 'warning' | 'critical'

export interface EvaluationIssue {
  id: string
  origin: 'rule' | 'judge'
  dimension: EvaluationDimension
  severity: IssueSeverity
  title: string
  explanation: string
  script_quote: string | null
  script_start: number | null
  script_end: number | null
  article_evidence_quote: string | null
  evidence_start: number | null
  evidence_end: number | null
  evidence_status: 'verified' | 'insufficient' | 'not_applicable'
}

export interface EvaluationSummary {
  id: string
  script_version_id: string
  job_id: string
  status: EvaluationStatus
  factual_consistency: number | null
  content_coverage: number | null
  audio_readiness: number | null
  rule_version: string
  provider: string | null
  requested_model: string | null
  actual_model: string | null
  prompt_version: string
  created_at: string
  started_at: string | null
  completed_at: string | null
}

export interface Evaluation extends EvaluationSummary {
  issues: EvaluationIssue[]
}

export interface EvaluationCreateResult {
  job: ProductionJob
  evaluation: EvaluationSummary
  idempotent_replay: boolean
}

export interface Approval {
  id: string
  script_version_id: string
  evaluation_id: string
  acknowledged_critical_issue_ids: string[]
  created_at: string
}

export interface Voice {
  id: string
  name: string
  provider: string
  supported_languages: Language[]
  verified_languages: string[]
  labels: Record<string, string>
  preview_url: string | null
}

export interface AudioAsset {
  id: string
  article_id: string
  article_title: string
  script_version_id: string
  script_version: number
  approval_id: string
  job_id: string
  provider: string
  model: string
  language: Language
  voice_id: string
  voice_name: string
  speed: number
  format: string
  mime_type: string
  duration_seconds: number
  file_size: number
  input_characters: number
  status: 'available' | 'deleting' | 'delete_failed' | 'deleted'
  delete_error: string | null
  created_at: string
  deleted_at: string | null
}

export interface AudioCreateResult {
  job: ProductionJob
  audio: AudioAsset | null
  idempotent_replay: boolean
  reused_existing: boolean
}

export interface AudioDetail extends AudioAsset {
  article: ArticleSummary
  job: ProductionJob
  provider_calls: {
    id: string
    status: string
    provider_request_id: string | null
    input_characters: number | null
    provider_credits: number | null
    latency_ms: number | null
  }[]
}

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
  voices: (language: Language) => request<{ items: Voice[]; cached: boolean }>(`/tts/voices?language=${language}`),
  audioList: (params: { scriptId?: string; q?: string; offset?: number } = {}) => {
    const query = new URLSearchParams({ limit: '20', offset: String(params.offset ?? 0), q: params.q ?? '' })
    if (params.scriptId) query.set('script_id', params.scriptId)
    return request<Page<AudioAsset>>(`/audio?${query}`)
  },
  audio: (id: string) => request<AudioDetail>(`/audio/${id}`),
  createAudio: (scriptId: string, body: { language: Language; voice_id: string; speed: number }, key: string) =>
    request<AudioCreateResult>(`/scripts/${scriptId}/audio`, {
      method: 'POST', headers: { 'Idempotency-Key': key }, body: JSON.stringify(body),
    }),
  audioJobs: (scriptId: string) => request<Page<ProductionJob>>(`/jobs?script_id=${scriptId}&type=audio_generation&limit=20&offset=0`),
  audioStream: (id: string) => `${BASE_URL}/audio/${id}/stream`,
  audioDownload: (id: string) => `${BASE_URL}/audio/${id}/download`,
  deleteAudio: (id: string) => request<void>(`/audio/${id}`, { method: 'DELETE' }),
  articles: (offset: number, q: string) =>
    request<Page<ArticleSummary>>(`/articles?limit=20&offset=${offset}&q=${encodeURIComponent(q)}`),
  article: (id: string) => request<Article>(`/articles/${id}`),
  imports: (offset: number) => request<Page<ImportAttempt>>(`/articles/imports?limit=20&offset=${offset}`),
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
  jobs: (articleId: string) =>
    request<Page<ProductionJob>>(`/jobs?article_id=${encodeURIComponent(articleId)}&type=script_generation&limit=20&offset=0`),
  job: (id: string) => request<ProductionJob>(`/jobs/${id}`),
  jobEvents: (id: string) => request<{ items: JobEvent[] }>(`/jobs/${id}/events`),
  scripts: (articleId: string) => request<Page<ScriptSummary>>(`/articles/${articleId}/scripts?limit=100&offset=0`),
  script: (id: string) => request<ScriptVersion>(`/scripts/${id}`),
  regenerate: (scriptId: string, config: GenerationConfig, idempotencyKey: string) =>
    request<JobCreateResult>(`/scripts/${scriptId}/regenerate`, {
      method: 'POST',
      headers: { 'Idempotency-Key': idempotencyKey },
      body: JSON.stringify({ config }),
    }),
  evaluations: (scriptId: string, limit = 20) =>
    request<Page<EvaluationSummary>>(`/scripts/${scriptId}/evaluations?limit=${limit}&offset=0`),
  evaluation: (id: string) => request<Evaluation>(`/evaluations/${id}`),
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
  approvals: (scriptId: string) =>
    request<{ items: Approval[] }>(`/scripts/${scriptId}/approvals`),
  approve: (
    scriptId: string,
    body: { evaluation_id: string; expected_version: number; acknowledged_critical_issue_ids: string[] },
  ) => request<Approval>(`/scripts/${scriptId}/approve`, { method: 'POST', body: JSON.stringify(body) }),
}



export interface JobExecution {
  id: string; job_id: string; attempt_no: number; stage: string; status: string
  heartbeat_at: string; lease_expires_at: string; started_at: string; completed_at: string | null
}
export interface ProviderCall {
  id: string; job_id: string; execution_id: string | null; operation: string; provider: string
  requested_model: string; actual_model: string | null; provider_request_id: string | null; status: string
  retryable: boolean; uncertain: boolean; prompt_tokens: number | null; completion_tokens: number | null
  total_tokens: number | null; cost: number | null; provider_credits: number | null; latency_ms: number | null
  error_code: string | null; error_message: string | null; created_at: string; completed_at: string | null
}
export interface Overview {
  total_jobs: number; status_counts: Record<JobStatus, number>; success_rate: number | null
  manual_retry_count: number; pending_review_count: number; unresolved_needs_attention_count: number
  active_processing: {average_seconds: number | null; sample_count: number; terminal_job_count: number}
  provider_latency: Record<string, Record<string, {average_ms: number | null; sample_count: number}>>
  usage: {openrouter_usd: number; elevenlabs_credits: number; unknown_cost_calls: number; mock_calls: number; total_calls: number}
  recent_audio: {id: string; article_title: string; script_version_id: string; created_at: string}[]
  recent_failures: {id: string; job_type: string; status: string; error_code: string | null; error_message: string | null}[]
}
