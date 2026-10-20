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
