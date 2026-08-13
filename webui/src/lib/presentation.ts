import type { TaskAllowedAction } from '@/types/product'

export const statusPresentation: Record<string, { label: string; tone: string; description: string }> = {
  queued: { label: '排队中', tone: 'neutral', description: '任务已创建，正在等待执行' },
  running: { label: '正在采集', tone: 'info', description: '抓取设备正在处理任务' },
  pausing: { label: '正在暂停', tone: 'warning', description: '将在当前页面安全保存后暂停' },
  paused: { label: '已暂停', tone: 'warning', description: '任务已暂停，可以继续或取消' },
  waiting_for_login: { label: '等待登录', tone: 'warning', description: '抖音登录已失效，需要重新连接' },
  waiting_for_space: { label: '磁盘空间不足', tone: 'warning', description: '媒体下载暂停，已采集数据不会丢失' },
  partial: { label: '部分完成', tone: 'warning', description: '部分阶段失败，可以只重试失败项' },
  completed: { label: '已完成', tone: 'success', description: '任务已经完成' },
  failed: { label: '失败', tone: 'danger', description: '任务执行失败，请查看原因' },
  cancelled: { label: '已取消', tone: 'neutral', description: '任务已取消，已有结果仍保留' },
  connected: { label: '已连接', tone: 'success', description: '账号可以用于采集' },
  session_expired: { label: '登录已过期', tone: 'warning', description: '请重新扫码连接' },
  verification_required: { label: '需要验证', tone: 'warning', description: '请等待管理员处理验证' },
  risk_controlled: { label: '暂时受限', tone: 'danger', description: '当前会话触发平台风险提示' },
  disconnected: { label: '已断开', tone: 'neutral', description: '账号连接已断开' },
  online: { label: '在线', tone: 'success', description: '执行设备在线' },
  offline: { label: '离线', tone: 'danger', description: '执行设备暂时不可用' },
}

export const actionLabels: Record<TaskAllowedAction, string> = {
  pause: '暂停', resume: '继续', cancel: '取消', reconnect: '处理登录',
  continue_after_login: '登录后继续', view_failures: '查看失败项', retry_failed: '只重试失败项',
  view_results: '查看结果', rerun: '再次运行', view_error: '查看原因',
}

export const stageLabels: Record<string, string> = {
  discover: '发现作品', detail: '作品详情', creator: '账号资料', comments: '评论',
  native_transcript: '原生字幕', media_download: '媒体下载', asr: '语音转写', finalize: '整理结果',
}

export function formatDate(value?: string | number | null) {
  if (!value) return '—'
  const date = typeof value === 'number' && value < 10_000_000_000 ? new Date(value * 1000) : new Date(value)
  return Number.isNaN(date.getTime()) ? '—' : new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(date)
}

export function formatBytes(value?: number | null) {
  if (!value) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1)
  return `${(value / 1024 ** index).toFixed(index > 1 ? 1 : 0)} ${units[index]}`
}

export function formatDuration(seconds?: number | null) {
  if (seconds == null) return '—'
  if (seconds < 60) return `${Math.round(seconds)} 秒`
  const minutes = Math.floor(seconds / 60)
  return seconds < 3600 ? `${minutes} 分钟` : `${Math.floor(minutes / 60)} 小时 ${minutes % 60} 分钟`
}
