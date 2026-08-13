import { Link } from 'react-router-dom'
import { AlertTriangle, ArrowRight, Captions, CircleUserRound, Clapperboard, FileText, MessageSquareText, Plus, Search, Sparkles, SquareStack, Tags, UserRoundSearch } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { EmptyState, ErrorState, PageHeader, ProgressBar, StatusBadge, Surface } from '@/components/product/Primitives'
import { useDashboardOverview } from '@/hooks/useProduct'
import { formatBytes, formatDate } from '@/lib/presentation'
import type { LucideIcon } from 'lucide-react'

const shortcuts = [
  { mode: 'search', label: '关键词', description: '按关键词发现公开视频', icon: Search },
  { mode: 'topic', label: '真实话题', description: '从真实话题页增量采集', icon: Tags },
  { mode: 'detail', label: '指定视频', description: '输入链接补取完整详情', icon: FileText },
  { mode: 'creator', label: '指定账号', description: '采集账号公开作品与指标', icon: UserRoundSearch },
]

export default function DashboardPage() {
  const query = useDashboardOverview()
  const data = query.data
  if (query.isError) return <ErrorState title="工作台暂时不可用" description="无法读取聚合数据，请确认 FlowLens API 正常运行。" retry={() => query.refetch()}/>
  const connection = data?.connection
  const taskCounts = data?.task_counts ?? {}
  const counts = data?.library_counts ?? {}
  const issues = data ? Object.entries(data.health_summary.checks).filter(([, check]) => !check.ok) : []
  return <div>
    <PageHeader eyebrow="工作台" title="今天想采集什么？" description="连接抖音账号后，用一个任务完成作品、评论、字幕、指标和媒体采集。" actions={<Button asChild size="lg"><Link to="/crawl/new"><Plus/>新建采集任务</Link></Button>}/>
    <div className="grid gap-4 xl:grid-cols-[1.25fr_.75fr]">
      <Surface className="overflow-hidden"><div className="flex flex-col gap-5 p-5 sm:flex-row sm:items-center sm:justify-between sm:p-6"><div className="flex items-center gap-4"><div className="grid h-12 w-12 place-items-center rounded-xl bg-teal-50 text-teal-700"><CircleUserRound className="h-6 w-6"/></div><div><p className="text-xs font-medium text-slate-500">当前抖音账号</p><div className="mt-1 flex items-center gap-2"><h2 className="text-lg font-semibold text-slate-950">{connection?.masked_nickname || (connection ? '待验证账号' : '尚未连接')}</h2>{connection ? <StatusBadge status={connection.status}/> : null}</div><p className="mt-1 text-xs text-slate-500">{connection?.last_verified_at ? `最后验证 ${formatDate(connection.last_verified_at)}` : '连接后即可开始远程采集'}</p></div></div><Button asChild variant={connection ? 'outline' : 'default'}><Link to="/connect">{connection ? '管理账号' : '连接抖音账号'}<ArrowRight/></Link></Button></div>
        <div className="grid grid-cols-2 border-t border-slate-200 sm:grid-cols-4">{shortcuts.map(item => <Link key={item.mode} to={`/crawl/new?mode=${item.mode}`} className="group border-b border-r border-slate-200 p-4 transition-colors hover:bg-teal-50/50 sm:border-b-0 last:border-r-0"><item.icon className="h-5 w-5 text-slate-400 transition-colors group-hover:text-teal-700"/><p className="mt-3 text-sm font-semibold text-slate-900">{item.label}</p><p className="mt-1 text-xs leading-5 text-slate-500">{item.description}</p></Link>)}</div>
      </Surface>
      <Surface className="p-5 sm:p-6"><div className="flex items-center justify-between"><div><p className="text-sm font-semibold text-slate-900">任务概览</p><p className="mt-1 text-xs text-slate-500">最近任务的实时状态</p></div><SquareStack className="h-5 w-5 text-slate-400"/></div><div className="mt-5 grid grid-cols-2 gap-3">{[
        ['正在运行', (taskCounts.running ?? 0) + (taskCounts.pausing ?? 0), 'text-sky-700 bg-sky-50'],
        ['排队中', taskCounts.queued ?? 0, 'text-slate-700 bg-slate-100'],
        ['等待登录', taskCounts.waiting_for_login ?? 0, 'text-amber-800 bg-amber-50'],
        ['需要处理', (taskCounts.failed ?? 0) + (taskCounts.partial ?? 0), 'text-red-700 bg-red-50'],
      ].map(([label, value, tone]) => <div key={String(label)} className={`rounded-lg p-3 ${tone}`}><p className="text-2xl font-semibold tabular-nums">{value}</p><p className="mt-1 text-xs font-medium">{label}</p></div>)}</div><Button asChild variant="ghost" className="mt-4 w-full justify-between"><Link to="/tasks">查看全部任务<ArrowRight/></Link></Button></Surface>
    </div>
    {issues.length ? <div className="mt-4 rounded-xl border border-amber-200 bg-amber-50 p-4"><div className="flex gap-3"><AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-amber-700"/><div><p className="text-sm font-semibold text-amber-900">有 {issues.length} 项环境能力需要关注</p><p className="mt-1 text-sm text-amber-800">{issues.map(([name]) => ({cdp:'Chrome 未连接',faster_whisper:'ASR 环境不可用',ffprobe:'未安装 ffprobe',sqlite_fts5:'全文检索降级',media_writable:'媒体目录不可写',task_database:'任务库不可用'}[name] || name)).join('、')}</p><Link to="/settings" className="mt-2 inline-flex text-sm font-semibold text-amber-900 underline underline-offset-4">查看系统设置</Link></div></div></div> : null}
    <div className="mt-6 grid gap-4 lg:grid-cols-[1.5fr_.7fr]">
      <Surface><div className="flex items-center justify-between border-b border-slate-200 px-5 py-4"><div><h2 className="text-sm font-semibold text-slate-900">最近任务</h2><p className="mt-1 text-xs text-slate-500">显示最近五次采集</p></div><Link to="/tasks" className="text-sm font-medium text-teal-700 hover:text-teal-900">全部任务</Link></div>{data?.recent_runs.length ? <div>{data.recent_runs.map(run => <Link to={`/tasks/${run.run_id}`} key={run.run_id} className="block border-b border-slate-100 px-5 py-4 last:border-0 hover:bg-slate-50"><div className="flex items-start justify-between gap-3"><div className="min-w-0"><p className="truncate text-sm font-semibold text-slate-900">{run.display_name}</p><p className="mt-1 text-xs text-slate-500">{run.account_label} · {formatDate(run.created_at)} · {run.stage_label}</p></div><StatusBadge status={run.status} label={run.status_label}/></div><div className="mt-3"><ProgressBar value={run.progress.percent}/><p className="mt-1.5 text-right text-xs tabular-nums text-slate-500">{run.progress.completed}/{run.progress.total || '—'} 个作品</p></div></Link>)}</div> : <EmptyState title="还没有采集任务" description="从关键词、话题、视频或账号创建第一个任务。" action={<Button asChild><Link to="/crawl/new">新建采集</Link></Button>}/>}</Surface>
      <Surface className="p-5"><div className="flex items-center gap-2"><Sparkles className="h-5 w-5 text-teal-700"/><h2 className="text-sm font-semibold text-slate-900">内容摘要</h2></div><div className="mt-5 space-y-4">{([
        ['作品', counts.awemes ?? 0, FileText], ['评论与回复', counts.comments ?? 0, MessageSquareText], ['字幕', counts.transcripts ?? 0, Captions], ['本地媒体', counts.media ?? 0, Clapperboard],
      ] as Array<[string, number, LucideIcon]>).map(([label, count, Icon]) => <div key={label} className="flex items-center justify-between"><span className="flex items-center gap-3 text-sm text-slate-600"><span className="grid h-8 w-8 place-items-center rounded-lg bg-slate-100"><Icon className="h-4 w-4"/></span>{label}</span><strong className="text-lg font-semibold tabular-nums text-slate-950">{count}</strong></div>)}</div><div className="mt-5 rounded-lg bg-slate-50 p-3 text-xs leading-5 text-slate-500">媒体已使用 {formatBytes(data?.storage_summary.media_bytes)}，磁盘剩余 {formatBytes(data?.storage_summary.free_bytes)}。</div></Surface>
    </div>
  </div>
}
