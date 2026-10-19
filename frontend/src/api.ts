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
}

