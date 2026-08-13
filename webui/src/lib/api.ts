import axios from 'axios'

const api = axios.create({
  baseURL: '/api',
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json',
  },
})

// Types
export interface CrawlerConfig {
  platform: string
  login_type: string
  crawler_type: string
  keywords: string
  start_page: number
  enable_comments: boolean
  enable_sub_comments: boolean
  save_option: string
  cookies: string
  headless: boolean
}

export interface CrawlerStatus {
  status: 'idle' | 'running' | 'stopping' | 'error'
  platform: string | null
  crawler_type: string | null
  started_at: string | null
  error_message: string | null
}

export interface DouyinProgressItem {
  scope: string
  scope_id: string
  cursor: string
  sub_cursor: string
  status: 'running' | 'complete' | 'partial' | 'failed'
  expected_count: number | null
  collected_count: number
  last_error: string
  updated_at: number
}

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

export interface FilePreviewResponse {
  data: Record<string, unknown>[]
  total: number
  columns?: string[]
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

// API functions
export const crawlerApi = {
  start: (config: CrawlerConfig | Record<string, unknown>) => api.post('/crawler/start', config),
  stop: () => api.post('/crawler/stop'),
  getStatus: () => api.get<CrawlerStatus>('/crawler/status'),
  getLogs: (limit = 100) => api.get<{ logs: LogEntry[] }>('/crawler/logs', { params: { limit } }),
  getProgress: (limit = 100) => api.get<{ platform: string; items: DouyinProgressItem[] }>(
    '/crawler/progress', { params: { limit } },
  ),
}

export const dataApi = {
  getFiles: (platform?: string, fileType?: string) =>
    api.get<{ files: DataFile[] }>('/data/files', { params: { platform, file_type: fileType } }),
  getFileContent: (path: string, limit = 100) =>
    api.get<FilePreviewResponse>('/data/files/' + path, { params: { preview: true, limit } }),
  getStats: () => api.get('/data/stats'),
  getDownloadUrl: (path: string) => `/api/data/download/${path}`,
}

export const configApi = {
  getPlatforms: () => api.get<{ platforms: Platform[] }>('/config/platforms'),
  getOptions: () =>
    api.get<{
      login_types: ConfigOption[]
      crawler_types: ConfigOption[]
      save_options: ConfigOption[]
    }>('/config/options'),
}

export interface EnvCheckResult {
  success: boolean
  message: string
  output?: string
  error?: string
}

export const envApi = {
  check: () => api.get<EnvCheckResult>('/env/check'),
}

export const taskApi = {
  list: (params: {limit?:number;offset?:number;status?:string} = {}) => api.get<{items: import('@/types/product').TaskSummary[];total:number;status_counts:Record<string,number>;limit:number;offset:number}>('/tasks',{params}),
  detail: (id:string) => api.get<import('@/types/product').TaskSummary & Record<string, unknown>>(`/tasks/${id}`),
  items: (id:string) => api.get(`/tasks/${id}/items`),
  logs: (id:string) => api.get(`/tasks/${id}/logs`),
  pause: (id:string) => api.post(`/tasks/${id}/pause`),
  resume: (id:string) => api.post(`/tasks/${id}/resume`),
  continueAfterLogin: (id:string) => api.post(`/tasks/${id}/continue-after-login`),
  cancel: (id:string) => api.post(`/tasks/${id}/cancel`),
  retry: (id:string) => api.post(`/tasks/${id}/retry-failed`),
  rerun: (id:string) => api.post<{status:string;run_id:string;source_run_id:string}>(`/tasks/${id}/rerun`),
  removeHistory: (id:string) => api.delete(`/tasks/${id}`, {params:{confirm:true}}),
}
export const mediaApi = {
  list: (params: {limit?:number;offset?:number;q?:string;kind?:string;status?:string;sort?:string;aweme_id?:string} = {}) => api.get<import('@/types/product').MediaListResponse>('/media',{params}),
  streamUrl: (id:string) => `/api/media/${id}/stream`,
  remove: (id:string) => api.delete(`/media/${id}`, {params:{confirm:true}}),
}
export const libraryApi = {
  awemes: (q='', filters:Record<string,unknown>={}) => api.get<{items: import('@/types/crawler').Aweme[];total:number}>('/library/awemes',{params:{q,...filters}}),
  creators: (q='') => api.get('/library/creators',{params:{q}}),
  topics: (q='') => api.get('/library/topics',{params:{q}}),
  comments: (q='') => api.get('/library/comments',{params:{q}}),
  transcripts: (q='') => api.get('/library/transcripts',{params:{q}}),
  detail: (id:string) => api.get(`/library/awemes/${id}`),
  search: (q:string) => api.get('/library/search',{params:{q}}),
  stats: () => api.get('/library/stats'),
  exportUrl: (format:'jsonl'|'csv',q='',filters:Record<string,unknown>={}) => {
    const params = new URLSearchParams({format,q})
    Object.entries(filters).forEach(([key,value])=>{if(value!==''&&value!=null)params.set(key,String(value))})
    return `/api/library/export?${params.toString()}`
  },
}
export const scheduleApi = {
  list: () => api.get<{items: Record<string,unknown>[]}>('/schedules'),
  create: (data:Record<string,unknown>) => api.post('/schedules',data),
  update: (id:string,data:Record<string,unknown>) => api.put(`/schedules/${id}`,data),
  run: (id:string) => api.post(`/schedules/${id}/run-now`),
  remove: (id:string) => api.delete(`/schedules/${id}`),
}

const remoteHeaders = () => {
  const token = import.meta.env.VITE_FLOWLENS_PROXY_TOKEN as string | undefined
  const user = import.meta.env.VITE_FLOWLENS_USER_ID as string | undefined
  return token && user ? {'X-FlowLens-Proxy-Token':token, 'X-FlowLens-User-ID':user} : {}
}

export const systemApi = {
  health:()=>api.get('/system/health'),
  storage:()=>api.get('/system/storage'),
  capabilities: () => api.get<import('@/types/product').Capabilities>('/system/capabilities', {headers:remoteHeaders()}),
}

export const productApi = {
  capabilities: systemApi.capabilities,
  overview: () => api.get<import('@/types/product').DashboardOverview>('/dashboard/overview', {headers:remoteHeaders()}),
}

export const remoteApi = {
  workers: () => api.get('/flowlens/workers',{headers:remoteHeaders()}),
  connections: () => api.get('/flowlens/douyin/connections',{headers:remoteHeaders()}),
  createLogin: (worker_id:string) => api.post('/flowlens/douyin/login-sessions',{worker_id},{headers:remoteHeaders()}),
  login: (id:string) => api.get(`/flowlens/douyin/login-sessions/${id}`,{headers:remoteHeaders()}),
  qr: (id:string) => api.get(`/flowlens/douyin/login-sessions/${id}/qr`,{headers:remoteHeaders(),responseType:'blob'}),
  refreshLogin: (id:string) => api.post(`/flowlens/douyin/login-sessions/${id}/refresh`,{}, {headers:remoteHeaders()}),
  cancelLogin: (id:string) => api.post(`/flowlens/douyin/login-sessions/${id}/cancel`,{}, {headers:remoteHeaders()}),
  disconnect: (id:string) => api.delete(`/flowlens/douyin/connections/${id}`,{headers:remoteHeaders(),params:{confirm:true}}),
  reconnect: (id:string) => api.post(`/flowlens/douyin/connections/${id}/login-session`,{}, {headers:remoteHeaders()}),
  runs: (params: {limit?:number;offset?:number;status?:string} = {}) => api.get<{items: import('@/types/product').TaskSummary[];total:number;status_counts:Record<string,number>;limit:number;offset:number}>('/flowlens/crawl-runs',{headers:remoteHeaders(),params}),
  run: (id:string) => api.get(`/flowlens/crawl-runs/${id}`,{headers:remoteHeaders()}),
  runItems: (id:string) => api.get(`/flowlens/crawl-runs/${id}/items`,{headers:remoteHeaders()}),
  runLogs: (id:string) => api.get(`/flowlens/crawl-runs/${id}/logs`,{headers:remoteHeaders()}),
  createRun: (data:Record<string,unknown>) => api.post('/flowlens/crawl-runs',data,{headers:remoteHeaders()}),
  control: (id:string, action:'pause'|'resume'|'cancel'|'retry-failed') => api.post(`/flowlens/crawl-runs/${id}/${action}`,{}, {headers:remoteHeaders()}),
  rerun: (id:string) => api.post<{status:string;run_id:string;source_run_id:string}>(`/flowlens/crawl-runs/${id}/rerun`,{}, {headers:remoteHeaders()}),
  removeRunHistory: (id:string) => api.delete(`/flowlens/crawl-runs/${id}`, {headers:remoteHeaders(),params:{confirm:true}}),
  results: (kind:string, params: {limit?:number;offset?:number} = {}) => api.get(`/flowlens/results/${kind}`,{headers:remoteHeaders(),params}),
  resultDetail: (id:string) => api.get(`/flowlens/results/aweme/${id}/detail`,{headers:remoteHeaders()}),
  mediaUrl: (id:string) => `/api/flowlens/media/${id}/stream`,
  enrollment: () => api.post('/flowlens/admin/worker-enrollments',{}, {headers:{...remoteHeaders(),'X-FlowLens-Role':'admin'}}),
  adminWorkers: () => api.get('/flowlens/admin/workers',{headers:{...remoteHeaders(),'X-FlowLens-Role':'admin'}}),
}

export default api
