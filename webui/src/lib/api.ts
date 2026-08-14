import axios from "axios";

const api = axios.create({
  baseURL: "/api",
  timeout: 30000,
  withCredentials: true,
  headers: {
    "Content-Type": "application/json",
  },
});

let csrfToken: string | null = null;
export const setCsrfToken = (value: string | null) => {
  csrfToken = value;
};
api.interceptors.request.use((config) => {
  const method = (config.method || "get").toLowerCase();
  if (csrfToken && ["post", "put", "patch", "delete"].includes(method)) {
    config.headers.set("X-CSRF-Token", csrfToken);
  }
  return config;
});

// Types
export interface CrawlerConfig {
  platform: string;
  login_type: string;
  crawler_type: string;
  keywords: string;
  start_page: number;
  enable_comments: boolean;
  enable_sub_comments: boolean;
  save_option: string;
  cookies: string;
  headless: boolean;
}

export interface CrawlerStatus {
  status: "idle" | "running" | "stopping" | "error";
  platform: string | null;
  crawler_type: string | null;
  started_at: string | null;
  error_message: string | null;
}

export interface DouyinProgressItem {
  scope: string;
  scope_id: string;
  cursor: string;
  sub_cursor: string;
  status: "running" | "complete" | "partial" | "failed";
  expected_count: number | null;
  collected_count: number;
  last_error: string;
  updated_at: number;
}

export interface LogEntry {
  id: number;
  timestamp: string;
  level: "info" | "warning" | "error" | "success" | "debug";
  message: string;
}

export interface DataFile {
  name: string;
  path: string;
  size: number;
  modified_at: number;
  record_count: number | null;
  type: string;
}

export interface FilePreviewResponse {
  data: Record<string, unknown>[];
  total: number;
  columns?: string[];
}

export interface Platform {
  value: string;
  label: string;
  icon: string;
}

export interface ConfigOption {
  value: string;
  label: string;
}

// API functions
export const crawlerApi = {
  start: (config: CrawlerConfig | Record<string, unknown>) =>
    api.post("/crawler/start", config),
  stop: () => api.post("/crawler/stop"),
  getStatus: () => api.get<CrawlerStatus>("/crawler/status"),
  getLogs: (limit = 100) =>
    api.get<{ logs: LogEntry[] }>("/crawler/logs", { params: { limit } }),
  getProgress: (limit = 100) =>
    api.get<{ platform: string; items: DouyinProgressItem[] }>(
      "/crawler/progress",
      { params: { limit } },
    ),
};

export const dataApi = {
  getFiles: (platform?: string, fileType?: string) =>
    api.get<{ files: DataFile[] }>("/data/files", {
      params: { platform, file_type: fileType },
    }),
  getFileContent: (path: string, limit = 100) =>
    api.get<FilePreviewResponse>("/data/files/" + path, {
      params: { preview: true, limit },
    }),
  getStats: () => api.get("/data/stats"),
  getDownloadUrl: (path: string) => `/api/data/download/${path}`,
};

export const configApi = {
  getPlatforms: () => api.get<{ platforms: Platform[] }>("/config/platforms"),
  getOptions: () =>
    api.get<{
      login_types: ConfigOption[];
      crawler_types: ConfigOption[];
      save_options: ConfigOption[];
    }>("/config/options"),
};

export interface EnvCheckResult {
  success: boolean;
  message: string;
  output?: string;
  error?: string;
}

export const envApi = {
  check: () => api.get<EnvCheckResult>("/env/check"),
};

export const taskApi = {
  list: (params: { limit?: number; offset?: number; status?: string } = {}) =>
    api.get<{
      items: import("@/types/product").TaskSummary[];
      total: number;
      status_counts: Record<string, number>;
      limit: number;
      offset: number;
    }>("/tasks", { params }),
  detail: (id: string) =>
    api.get<import("@/types/product").TaskSummary & Record<string, unknown>>(
      `/tasks/${id}`,
    ),
  items: (id: string) => api.get(`/tasks/${id}/items`),
  logs: (id: string) => api.get(`/tasks/${id}/logs`),
  pause: (id: string) => api.post(`/tasks/${id}/pause`),
  resume: (id: string) => api.post(`/tasks/${id}/resume`),
  continueAfterLogin: (id: string) =>
    api.post(`/tasks/${id}/continue-after-login`),
  cancel: (id: string) => api.post(`/tasks/${id}/cancel`),
  retry: (id: string) => api.post(`/tasks/${id}/retry-failed`),
  rerun: (id: string) =>
    api.post<{ status: string; run_id: string; source_run_id: string }>(
      `/tasks/${id}/rerun`,
    ),
  removeHistory: (id: string) =>
    api.delete(`/tasks/${id}`, { params: { confirm: true } }),
};
export const mediaApi = {
  list: (
    params: {
      limit?: number;
      offset?: number;
      q?: string;
      kind?: string;
      status?: string;
      sort?: string;
      aweme_id?: string;
    } = {},
  ) =>
    api.get<import("@/types/product").MediaListResponse>("/media", { params }),
  streamUrl: (id: string) => `/api/media/${id}/stream`,
  remove: (id: string) =>
    api.delete(`/media/${id}`, { params: { confirm: true } }),
};
export const libraryApi = {
  awemes: (q = "", filters: Record<string, unknown> = {}) =>
    api.get<{ items: import("@/types/crawler").Aweme[]; total: number }>(
      "/library/awemes",
      { params: { q, ...filters } },
    ),
  creators: (q = "") => api.get("/library/creators", { params: { q } }),
  topics: (q = "") => api.get("/library/topics", { params: { q } }),
  comments: (q = "") => api.get("/library/comments", { params: { q } }),
  transcripts: (q = "") => api.get("/library/transcripts", { params: { q } }),
  detail: (id: string) => api.get(`/library/awemes/${id}`),
  search: (q: string) => api.get("/library/search", { params: { q } }),
  stats: () => api.get("/library/stats"),
  exportUrl: (
    format: "jsonl" | "csv",
    q = "",
    filters: Record<string, unknown> = {},
  ) => {
    const params = new URLSearchParams({ format, q });
    Object.entries(filters).forEach(([key, value]) => {
      if (value !== "" && value != null) params.set(key, String(value));
    });
    return `/api/library/export?${params.toString()}`;
  },
};
export const scheduleApi = {
  list: () => api.get<{ items: Record<string, unknown>[] }>("/schedules"),
  create: (data: Record<string, unknown>) => api.post("/schedules", data),
  update: (id: string, data: Record<string, unknown>) =>
    api.put(`/schedules/${id}`, data),
  run: (id: string) => api.post(`/schedules/${id}/run-now`),
  remove: (id: string) => api.delete(`/schedules/${id}`),
};

export const systemApi = {
  health: () => api.get("/system/health"),
  storage: () => api.get("/system/storage"),
  capabilities: () =>
    api.get<import("@/types/product").Capabilities>("/system/capabilities"),
};

export const productApi = {
  capabilities: systemApi.capabilities,
  overview: () =>
    api.get<import("@/types/product").DashboardOverview>("/dashboard/overview"),
};

export const remoteApi = {
  workers: () => api.get("/flowlens/workers"),
  connections: () => api.get("/flowlens/douyin/connections"),
  createLogin: () => api.post("/flowlens/douyin/login-sessions", {}),
  login: (id: string) => api.get(`/flowlens/douyin/login-sessions/${id}`),
  qr: (id: string) =>
    api.get(`/flowlens/douyin/login-sessions/${id}/qr`, {
      responseType: "blob",
    }),
  refreshLogin: (id: string) =>
    api.post(`/flowlens/douyin/login-sessions/${id}/refresh`, {}),
  cancelLogin: (id: string) =>
    api.post(`/flowlens/douyin/login-sessions/${id}/cancel`, {}),
  disconnect: (id: string) =>
    api.delete(`/flowlens/douyin/connections/${id}`, {
      params: { confirm: true },
    }),
  reconnect: (id: string) =>
    api.post(`/flowlens/douyin/connections/${id}/login-session`, {}),
  updateConnection: (
    id: string,
    data: { display_name?: string; remark?: string },
  ) => api.patch(`/flowlens/douyin/connections/${id}`, data),
  runs: (
    params: {
      limit?: number;
      offset?: number;
      status?: string;
      connection_id?: string;
    } = {},
  ) =>
    api.get<{
      items: import("@/types/product").TaskSummary[];
      total: number;
      status_counts: Record<string, number>;
      limit: number;
      offset: number;
    }>("/flowlens/crawl-runs", { params }),
  run: (id: string) => api.get(`/flowlens/crawl-runs/${id}`),
  runItems: (id: string) => api.get(`/flowlens/crawl-runs/${id}/items`),
  runLogs: (id: string) => api.get(`/flowlens/crawl-runs/${id}/logs`),
  createRun: (data: Record<string, unknown>) =>
    api.post("/flowlens/crawl-runs", data),
  control: (
    id: string,
    action: "pause" | "resume" | "cancel" | "retry-failed",
  ) => api.post(`/flowlens/crawl-runs/${id}/${action}`, {}),
  rerun: (id: string) =>
    api.post<{ status: string; run_id: string; source_run_id: string }>(
      `/flowlens/crawl-runs/${id}/rerun`,
      {},
    ),
  removeRunHistory: (id: string) =>
    api.delete(`/flowlens/crawl-runs/${id}`, { params: { confirm: true } }),
  results: (
    kind: string,
    params: { limit?: number; offset?: number; connection_id?: string } = {},
  ) => api.get(`/flowlens/results/${kind}`, { params }),
  resultDetail: (id: string) => api.get(`/flowlens/results/aweme/${id}/detail`),
  mediaUrl: (id: string) => `/api/flowlens/media/${id}/stream`,
  deleteMedia: (id: string) =>
    api.delete(`/flowlens/media/${id}`, { params: { confirm: true } }),
  enrollment: () => api.post("/flowlens/admin/worker-enrollments", {}),
  adminWorkers: () => api.get("/flowlens/admin/workers"),
  adminQueue: () => api.get("/flowlens/admin/queue"),
  adminPauseRun: (id: string) =>
    api.post(`/flowlens/admin/queue/${id}/pause`, {}),
  adminVerifications: () => api.get("/flowlens/admin/verifications"),
  adminRecheckVerification: (id: string) =>
    api.post(`/flowlens/admin/verifications/${id}/recheck`, {}),
  revokeWorker: (id: string) =>
    api.delete(`/flowlens/admin/workers/${id}`, { params: { confirm: true } }),
  closeBrowser: (connectionId: string) =>
    api.post(`/flowlens/admin/browser/${connectionId}/close`, {}),
};

export interface AuthUser {
  user_id: string;
  username: string;
  display_name: string;
  role: "admin" | "user";
  status: string;
  must_change_password: boolean;
  max_douyin_connections?: number;
  max_queued_tasks?: number;
  media_quota_bytes?: number;
}
export interface AuthResponse {
  user: AuthUser;
  csrf_token: string | null;
  capabilities: {
    admin_console: boolean;
    multiple_douyin_connections: boolean;
  };
}
export const authApi = {
  login: (username: string, password: string) =>
    api.post<AuthResponse>("/auth/login", { username, password }),
  me: () => api.get<AuthResponse>("/auth/me"),
  changePassword: (new_password: string, confirm_password: string) =>
    api.post<AuthResponse>("/auth/change-password", {
      new_password,
      confirm_password,
    }),
  logout: () => api.post("/auth/logout", {}),
  logoutAll: () => api.post("/auth/logout-all", {}),
};

export interface AdminUser {
  user_id: string;
  username: string;
  display_name: string;
  role: "admin" | "user";
  status: string;
  must_change_password: boolean;
  max_douyin_connections: number;
  max_queued_tasks: number;
  media_quota_bytes: number;
  douyin_connection_count?: number;
  active_task_count?: number;
  media_usage_bytes?: number;
  created_at: string;
  last_login_at?: string;
  suspended_at?: string;
}
export const adminUserApi = {
  list: (
    params: {
      search?: string;
      status?: string;
      limit?: number;
      offset?: number;
    } = {},
  ) =>
    api.get<{ items: AdminUser[]; total: number }>("/admin/users", { params }),
  create: (data: Record<string, unknown>) =>
    api.post<{
      user: AdminUser;
      temporary_password: string;
      temporary_password_expires_at: string;
    }>("/admin/users", data),
  update: (id: string, data: Record<string, unknown>) =>
    api.patch<AdminUser>(`/admin/users/${id}`, data),
  resetPassword: (id: string) =>
    api.post<{
      temporary_password: string;
      temporary_password_expires_at: string;
    }>(`/admin/users/${id}/reset-temporary-password`, {}),
  revokeSessions: (id: string) =>
    api.post(`/admin/users/${id}/revoke-sessions`, {}),
  suspend: (id: string) => api.post(`/admin/users/${id}/suspend`, {}),
  restore: (id: string) => api.post(`/admin/users/${id}/restore`, {}),
};

export default api;
