export interface CrawlerConfig {
  platform: string
  login_type: string
  crawler_type: string
  keywords: string
  specified_ids: string  // 详情模式下的帖子/视频ID
  creator_ids: string    // 创作者模式下的创作者ID
  topics: string
  start_page: number
  enable_comments: boolean
  enable_sub_comments: boolean
  save_option: string
  cookies: string
  headless: boolean
  max_notes_count: number
  max_comments_count: number
  enable_creator_profile: boolean
  force_creator_refresh: boolean
  enable_native_subtitle: boolean
  enable_asr: boolean
  asr_model: string
  asr_language: string
  save_raw_payload: boolean
  keep_media: boolean
  download_media: boolean
  download_video: boolean
  download_images: boolean
  download_cover: boolean
  download_music: boolean
  media_quality: 'best_h264'
  max_media_downloads: number
  max_media_total_bytes: number
  media_library_max_bytes: number
  min_free_disk_bytes: number
  skip_existing_media: boolean
  verify_media: boolean
  keep_asr_source_media: boolean
  incremental: boolean
  stop_after_existing: number
  refresh_existing_metrics: boolean
  refresh_existing_comments: boolean
  enable_ip_proxy: boolean
  static_proxy_url: string
  schedule_id?: string | null
}

export interface CrawlerStatus {
  status: 'idle' | 'running' | 'stopping' | 'error'
  platform: string | null
  crawler_type: string | null
  started_at: string | null
  error_message: string | null
  run_id?: string | null
}

export interface TaskRun { run_id:string; platform:string; crawler_type:string; status:string; stage:string; created_at:string; error_message?:string }
export interface MediaAsset { asset_id:string; aweme_id:string; kind:string; status:string; path?:string; size_bytes:number; mime_type?:string }
export interface Aweme { aweme_id:string; title:string; desc:string; nickname:string; liked_count?:number; comment_count?:number; source_topic?:string; collected_at:number }

export interface LogEntry {
  id: number
  timestamp: string
  level: 'info' | 'warning' | 'error' | 'success' | 'debug'
  message: string
}

export interface DataFile {
  name: string
  path: string
  size: number
  modified_at: number
  record_count: number | null
  type: string
}

export interface Platform {
  value: string
  label: string
  icon: string
}

export interface ConfigOption {
  value: string
  label: string
}
