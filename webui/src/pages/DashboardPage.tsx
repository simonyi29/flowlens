import type { LucideIcon } from 'lucide-react'
import {
  AlertTriangle,
  ArrowRight,
  Captions,
  CircleUserRound,
  Clapperboard,
  FileText,
  MessageSquareText,
  Plus,
  Search,
  Sparkles,
  Tags,
  UserRoundSearch,
} from 'lucide-react'
import { Link } from 'react-router-dom'
import { EmptyState, ErrorState, PageHeader, ProgressBar, StatusBadge, Surface } from '@/components/product/Primitives'
import { Button } from '@/components/ui/button'
import { useDashboardOverview } from '@/hooks/useProduct'
import { formatBytes, formatDate } from '@/lib/presentation'

const shortcuts = [
  { mode: 'search', label: '关键词', description: '按关键词发现公开视频', icon: Search },
  { mode: 'topic', label: '真实话题', description: '从话题页采集相关作品', icon: Tags },
  { mode: 'detail', label: '指定视频', description: '输入链接补取完整详情', icon: FileText },
  { mode: 'creator', label: '指定账号', description: '采集账号公开作品与指标', icon: UserRoundSearch },
]

const healthLabels: Record<string, string> = {
  cdp: 'Chrome 未连接',
  faster_whisper: 'ASR 环境不可用',
  ffprobe: '未安装 ffprobe',
  sqlite_fts5: '全文检索降级',
  media_writable: '媒体目录不可写',
  task_database: '任务库不可用',
}

export default function DashboardPage() {
  const query = useDashboardOverview()
  const data = query.data

  if (query.isError) {
    return <ErrorState title="工作台暂时不可用" description="无法读取聚合数据，请确认 FlowLens API 正常运行。" retry={() => query.refetch()}/>
  }

  const connection = data?.connection
  const taskCounts = data?.task_counts ?? {}
  const counts = data?.library_counts ?? {}
  const issues = data ? Object.entries(data.health_summary.checks).filter(([, check]) => !check.ok) : []
  const taskMetrics = [
    ['正在运行', (taskCounts.running ?? 0) + (taskCounts.pausing ?? 0), 'text-sky-700'],
    ['排队中', taskCounts.queued ?? 0, 'text-slate-700'],
    ['等待登录', taskCounts.waiting_for_login ?? 0, 'text-amber-700'],
    ['需要处理', (taskCounts.failed ?? 0) + (taskCounts.partial ?? 0), 'text-red-700'],
  ] as const

  return <div>
    <PageHeader
      eyebrow="工作台"
      title="数据采集概览"
      description="确认账号与运行环境，选择一种采集方式开始任务。"
      actions={<Button asChild size="lg"><Link to="/crawl/new"><Plus/>新建采集任务</Link></Button>}
    />

    <div className="space-y-8">
      <section className="grid gap-5 xl:grid-cols-[minmax(0,1.25fr)_minmax(320px,.75fr)]" aria-label="账号与任务状态">
        <Surface className="flex min-h-44 flex-col justify-between p-6 sm:p-7">
          <div className="flex flex-col gap-6 sm:flex-row sm:items-start sm:justify-between">
            <div className="flex items-start gap-4">
              <div className="grid h-12 w-12 shrink-0 place-items-center rounded-xl bg-teal-50 text-teal-700">
                <CircleUserRound className="h-6 w-6"/>
              </div>
              <div>
                <p className="text-xs font-medium text-slate-500">当前抖音账号</p>
                <div className="mt-2 flex flex-wrap items-center gap-2">
                  <h2 className="text-xl font-semibold tracking-tight text-slate-950">
                    {connection?.masked_nickname || (connection ? '待验证账号' : '尚未连接')}
                  </h2>
                  {connection ? <StatusBadge status={connection.status}/> : null}
                </div>
                <p className="mt-2 text-sm leading-6 text-slate-500">
                  {connection?.last_verified_at
                    ? `最后验证 ${formatDate(connection.last_verified_at)}`
                    : '连接账号后即可创建关键词、话题、视频或账号采集任务。'}
                </p>
              </div>
            </div>
            <Button asChild variant={connection ? 'outline' : 'default'} className="sm:shrink-0">
              <Link to="/connect">{connection ? '管理账号' : '连接抖音账号'}<ArrowRight/></Link>
            </Button>
          </div>
          <p className="mt-6 border-t border-slate-100 pt-4 text-xs leading-5 text-slate-500">
            浏览器会话保存在执行设备，网站只显示脱敏后的连接状态。
          </p>
        </Surface>

        <Surface className="min-h-44 p-6 sm:p-7">
          <div className="flex items-end justify-between gap-4">
            <div>
              <h2 className="text-base font-semibold text-slate-950">任务状态</h2>
              <p className="mt-1 text-sm text-slate-500">当前需要关注的任务</p>
            </div>
            <Link to="/tasks" className="inline-flex items-center gap-1 text-sm font-medium text-teal-700 hover:text-teal-900">
              全部任务<ArrowRight className="h-4 w-4"/>
            </Link>
          </div>
          <div className="mt-6 grid grid-cols-2 gap-x-8 gap-y-5">
            {taskMetrics.map(([label, value, tone]) => <div key={label}>
              <p className={`text-3xl font-semibold tabular-nums tracking-tight ${tone}`}>{value}</p>
              <p className="mt-1 text-xs font-medium text-slate-500">{label}</p>
            </div>)}
          </div>
        </Surface>
      </section>

      <section aria-labelledby="quick-start-title">
        <div className="mb-4 flex items-end justify-between gap-4">
          <div>
            <h2 id="quick-start-title" className="text-base font-semibold text-slate-950">快速开始</h2>
            <p className="mt-1 text-sm text-slate-500">选择采集来源，下一步再设置评论、字幕与下载范围。</p>
          </div>
        </div>
        <div className="grid gap-3 sm:grid-cols-2 2xl:grid-cols-4">
          {shortcuts.map(item => <Link
            key={item.mode}
            to={`/crawl/new?mode=${item.mode}`}
            className="group min-h-32 rounded-xl border border-slate-200 bg-white p-5 shadow-[0_1px_2px_rgba(15,23,42,.04)] transition-[border-color,box-shadow,transform] hover:-translate-y-0.5 hover:border-teal-300 hover:shadow-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-600 focus-visible:ring-offset-2"
          >
            <div className="flex items-start justify-between gap-4">
              <span className="grid h-10 w-10 place-items-center rounded-lg bg-slate-100 text-slate-500 transition-colors group-hover:bg-teal-50 group-hover:text-teal-700">
                <item.icon className="h-5 w-5"/>
              </span>
              <ArrowRight className="h-4 w-4 text-slate-300 transition-[color,transform] group-hover:translate-x-0.5 group-hover:text-teal-700"/>
            </div>
            <p className="mt-5 text-sm font-semibold text-slate-950">{item.label}</p>
            <p className="mt-1 text-sm leading-6 text-slate-500">{item.description}</p>
          </Link>)}
        </div>
      </section>

      {issues.length ? <section className="rounded-xl border border-amber-200 bg-amber-50 px-5 py-4" aria-label="环境提醒">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-start gap-3">
            <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-amber-700"/>
            <div>
              <p className="text-sm font-semibold text-amber-950">有 {issues.length} 项运行环境需要处理</p>
              <p className="mt-1 text-sm text-amber-800">{issues.map(([name]) => healthLabels[name] || name).join('、')}</p>
            </div>
          </div>
          <Button asChild variant="outline" size="sm" className="border-amber-300 bg-amber-50 text-amber-900 hover:bg-amber-100">
            <Link to="/settings">查看系统设置</Link>
          </Button>
        </div>
      </section> : null}

      <section className="grid gap-5 xl:grid-cols-[minmax(0,1.5fr)_minmax(300px,.7fr)]" aria-label="最近任务与内容摘要">
        <Surface>
          <div className="flex items-center justify-between border-b border-slate-200 px-5 py-4 sm:px-6">
            <div>
              <h2 className="text-base font-semibold text-slate-950">最近任务</h2>
              <p className="mt-1 text-sm text-slate-500">最近五次采集的状态和进度</p>
            </div>
            <Link to="/tasks" className="text-sm font-medium text-teal-700 hover:text-teal-900">全部任务</Link>
          </div>
          {data?.recent_runs.length ? <div>{data.recent_runs.map(run => <Link
            to={`/tasks/${run.run_id}`}
            key={run.run_id}
            className="block border-b border-slate-100 px-5 py-5 transition-colors last:border-0 hover:bg-slate-50 sm:px-6"
          >
            <div className="flex items-start justify-between gap-4">
              <div className="min-w-0">
                <p className="truncate text-sm font-semibold text-slate-950">{run.display_name}</p>
                <p className="mt-1.5 text-xs leading-5 text-slate-500">{run.account_label} · {formatDate(run.created_at)} · {run.stage_label}</p>
              </div>
              <StatusBadge status={run.status} label={run.status_label}/>
            </div>
            <div className="mt-4">
              <ProgressBar value={run.progress.percent}/>
              <p className="mt-2 text-right text-xs tabular-nums text-slate-500">{run.progress.completed}/{run.progress.total || '—'} 个作品</p>
            </div>
          </Link>)}</div> : <EmptyState
            title="还没有采集任务"
            description="从关键词、话题、视频或账号创建第一个任务。"
            action={<Button asChild><Link to="/crawl/new">新建采集</Link></Button>}
          />}
        </Surface>

        <Surface className="p-6">
          <div className="flex items-center gap-2">
            <Sparkles className="h-5 w-5 text-teal-700"/>
            <h2 className="text-base font-semibold text-slate-950">内容摘要</h2>
          </div>
          <div className="mt-6 space-y-5">{([
            ['作品', counts.awemes ?? 0, FileText],
            ['评论与回复', counts.comments ?? 0, MessageSquareText],
            ['字幕', counts.transcripts ?? 0, Captions],
            ['本地媒体', counts.media ?? 0, Clapperboard],
          ] as Array<[string, number, LucideIcon]>).map(([label, count, Icon]) => <div key={label} className="flex items-center justify-between gap-4">
            <span className="flex items-center gap-3 text-sm text-slate-600">
              <span className="grid h-9 w-9 place-items-center rounded-lg bg-slate-100"><Icon className="h-4 w-4"/></span>
              {label}
            </span>
            <strong className="text-xl font-semibold tabular-nums text-slate-950">{count}</strong>
          </div>)}</div>
          <div className="mt-6 border-t border-slate-100 pt-5 text-xs leading-5 text-slate-500">
            媒体已使用 {formatBytes(data?.storage_summary.media_bytes)}<br/>
            磁盘剩余 {formatBytes(data?.storage_summary.free_bytes)}
          </div>
        </Surface>
      </section>
    </div>
  </div>
}
