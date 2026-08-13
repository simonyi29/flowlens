import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { AlertCircle, ChevronRight, Clock3, Pause, Play, RefreshCw, RotateCcw, Square, SquareStack } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { EmptyState, PageHeader, ProgressBar, StatusBadge, Surface } from '@/components/product/Primitives'
import { remoteApi, taskApi } from '@/lib/api'
import { useCapabilities } from '@/hooks/useProduct'
import { actionLabels, formatDate, formatDuration } from '@/lib/presentation'
import type { TaskAllowedAction, TaskSummary } from '@/types/product'

const statusFilters = ['all','running','queued','waiting_for_login','partial','completed','failed']

export default function TasksPage() {
  const capabilities = useCapabilities(); const remote = capabilities.data?.features.remote_worker
  const [items, setItems] = useState<TaskSummary[]>([]), [filter, setFilter] = useState('all'), [loading, setLoading] = useState(true)
  const load = async () => { setLoading(true); try { const response = remote ? await remoteApi.runs() : await taskApi.list(); setItems(response.data.items) } finally { setLoading(false) } }
  useEffect(() => { if (capabilities.data) void load(); const timer = setInterval(() => { if (capabilities.data) void load() }, 5000); return () => clearInterval(timer) }, [remote, capabilities.data])
  const filtered = filter === 'all' ? items : items.filter(item => item.status === filter)
  const control = async (run: TaskSummary, action: TaskAllowedAction) => {
    if (action === 'rerun') { window.location.hash = `#/crawl/new?mode=${run.crawler_type || 'search'}`; return }
    if (action === 'view_results') { window.location.hash = '#/library'; return }
    if (action === 'view_failures' || action === 'view_error') { window.location.hash = `#/tasks/${run.run_id}`; return }
    if (action === 'reconnect') { window.location.hash = '#/connect'; return }
    if (action === 'cancel' && !confirm('取消任务后，已经保存的数据不会回滚。确认取消？')) return
    try {
      if (remote) await remoteApi.control(run.run_id, action === 'retry_failed' ? 'retry-failed' : action as 'pause'|'resume'|'cancel')
      else if (action === 'pause') await taskApi.pause(run.run_id)
      else if (action === 'resume') await taskApi.resume(run.run_id)
      else if (action === 'continue_after_login') await taskApi.continueAfterLogin(run.run_id)
      else if (action === 'cancel') await taskApi.cancel(run.run_id)
      else if (action === 'retry_failed') await taskApi.retry(run.run_id)
      toast.success('任务操作已提交'); await load()
    } catch { toast.error('当前任务状态不允许执行此操作') }
  }
  return <div><PageHeader eyebrow="工作台" title="任务中心" description="按采集来源识别任务，查看每个阶段的进度，并只显示当前可执行的操作。" actions={<Button variant="outline" onClick={load}><RefreshCw className={loading ? 'animate-spin' : ''}/>刷新</Button>}/>
    <div className="mb-4 flex gap-2 overflow-x-auto pb-1">{statusFilters.map(status => <button key={status} onClick={() => setFilter(status)} className={`min-h-9 whitespace-nowrap rounded-full px-3 text-xs font-medium ring-1 ring-inset ${filter === status ? 'bg-slate-950 text-white ring-slate-950' : 'bg-white text-slate-600 ring-slate-200 hover:bg-slate-50'}`}>{status === 'all' ? `全部 ${items.length}` : <>{({running:'运行中',queued:'排队中',waiting_for_login:'等待登录',partial:'部分完成',completed:'已完成',failed:'失败'} as Record<string,string>)[status]} {items.filter(item => item.status === status).length}</>}</button>)}</div>
    <Surface className="overflow-hidden">{filtered.length ? <><div className="hidden grid-cols-[minmax(240px,1.6fr)_130px_180px_145px_minmax(210px,auto)] gap-4 border-b border-slate-200 bg-slate-50 px-5 py-3 text-xs font-medium text-slate-500 lg:grid"><span>任务</span><span>状态</span><span>进度</span><span>创建时间</span><span className="text-right">操作</span></div><div>{filtered.map(run => <TaskRow key={run.run_id} run={run} onAction={control}/>)}</div></> : <EmptyState icon={<SquareStack/>} title={loading ? '正在加载任务' : '没有符合条件的任务'} description={loading ? '正在读取持久任务队列。' : '切换筛选条件，或者创建一个新的采集任务。'} action={!loading ? <Button asChild><Link to="/crawl/new">新建采集</Link></Button> : undefined}/>}</Surface>
  </div>
}

function TaskRow({ run, onAction }: { run: TaskSummary; onAction: (run:TaskSummary, action:TaskAllowedAction)=>void }) {
  const visible = run.allowed_actions.filter(action => !['continue_after_login'].includes(action)).slice(0, 3)
  return <article className="border-b border-slate-100 p-4 last:border-0 lg:grid lg:grid-cols-[minmax(240px,1.6fr)_130px_180px_145px_minmax(210px,auto)] lg:items-center lg:gap-4 lg:px-5"><div className="min-w-0"><Link to={`/tasks/${run.run_id}`} className="group flex items-start justify-between gap-3"><div className="min-w-0"><h2 className="truncate text-sm font-semibold text-slate-950 group-hover:text-teal-800">{run.display_name}</h2><p className="mt-1 truncate text-xs text-slate-500">{run.account_label} · {run.stage_label}</p></div><ChevronRight className="mt-1 h-4 w-4 shrink-0 text-slate-300 lg:hidden"/></Link></div><div className="mt-3 lg:mt-0"><StatusBadge status={run.status} label={run.status_label}/></div><div className="mt-4 lg:mt-0"><div className="flex items-center justify-between text-xs text-slate-500"><span>{run.progress.completed}/{run.progress.total || '—'} 个作品</span><span>{run.progress.percent}%</span></div><div className="mt-2"><ProgressBar value={run.progress.percent}/></div></div><div className="mt-3 text-xs text-slate-500 lg:mt-0"><p>{formatDate(run.created_at)}</p><p className="mt-1 flex items-center gap-1"><Clock3 className="h-3.5 w-3.5"/>{formatDuration(run.elapsed_seconds)}</p></div><div className="mt-4 flex flex-wrap gap-2 lg:mt-0 lg:justify-end">{visible.map(action => <Button key={action} size="sm" variant={action === 'cancel' ? 'ghost' : action === 'retry_failed' ? 'outline' : 'secondary'} onClick={() => onAction(run, action)}>{action === 'pause' ? <Pause/> : action === 'resume' ? <Play/> : action === 'cancel' ? <Square/> : action === 'retry_failed' || action === 'rerun' ? <RotateCcw/> : action === 'view_error' ? <AlertCircle/> : null}{actionLabels[action]}</Button>)}</div></article>
}
