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
  id: string
  job_type: 'script_generation'
  article_id: string
  source_script_version_id: string | null
  result_script_version_id: string | null
  status: JobStatus
  stage: string
  config: GenerationConfig
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
  const data: unknown = await response.json()
  if (!response.ok) {
    throw new ApiError(response.status, data as ApiErrorBody)
  }
  return data as T
}

export const api = {
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
    request<Page<ProductionJob>>(`/jobs?article_id=${encodeURIComponent(articleId)}&limit=20&offset=0`),
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
}

