export type AppMode = 'local' | 'remote'
export type UserRole = 'user' | 'admin'

export interface FeatureCapabilities {
  remote_worker: boolean
  local_crawl: boolean
  schedules: boolean
  media_stream: boolean
  asr: boolean
  admin: boolean
}

export interface Capabilities {
  mode: AppMode
  current_role: UserRole
  features: FeatureCapabilities
}

export interface ConnectionView {
  connection_id: string
  worker_id: string
  status: string
  creator_hash?: string
  masked_nickname?: string
  last_verified_at?: string
  created_at?: string
}

export type TaskAllowedAction =
  | 'pause' | 'resume' | 'cancel' | 'reconnect' | 'continue_after_login'
  | 'view_failures' | 'retry_failed' | 'view_results' | 'rerun' | 'view_error'

export interface StageCount {
  label: string
  status: string
  total: number
  completed: number
  failed: number
}

export interface TaskSummary {
  run_id: string
  platform?: string
  crawler_type?: string
  connection_id?: string
  status: string
  status_label: string
  stage: string
  stage_label: string
  display_name: string
  source_summary: string
  account_label: string
  progress: { completed: number; total: number; percent: number }
  stage_counts: Record<string, StageCount>
  allowed_actions: TaskAllowedAction[]
  created_at: string
  started_at?: string
  finished_at?: string
  elapsed_seconds?: number
  estimated_remaining_seconds?: number
  downloaded_bytes?: number
  task_media_quota_bytes?: number
  error_type?: string
  error_message?: string
  error?: ProductError
}

export interface ProductError {
  error_type: string
  user_message: string
  technical_detail?: string
  recoverable: boolean
  recommended_action: string
}

export interface HealthCheck {
  ok: boolean
  detail?: string
  [key: string]: unknown
}

export interface DashboardOverview {
  connection: ConnectionView | null
  task_counts: Record<string, number>
  recent_runs: TaskSummary[]
  library_counts: Record<string, number>
  health_summary: { status: string; checks: Record<string, HealthCheck> }
  storage_summary: {
    media_bytes: number
    free_bytes: number
    total_bytes: number
    library_limit_bytes: number
    min_free_bytes: number
  }
}

export interface ContentSummary {
  aweme_id: string
  title?: string
  desc?: string
  nickname?: string
  creator_hash?: string
  source_keyword?: string
  source_topic?: string
  publish_time?: number
  collected_at?: number
  liked_count?: number
  comment_count?: number
  play_count?: number
  cover_url?: string
  transcript_status?: string
  [key: string]: unknown
}

export interface MediaSummary {
  asset_id: string
  aweme_id: string
  run_id?: string
  creator_hash?: string
  kind: string
  status: string
  size_bytes: number
  mime_type?: string
  quality?: string
  duration_ms?: number
  sha256?: string
  [key: string]: unknown
}
