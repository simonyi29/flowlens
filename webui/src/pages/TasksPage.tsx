import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate } from 'react-router-dom'
import {
  AlertCircle, ChevronLeft, ChevronRight, Clock3, Eye, Pause, Play,
  RefreshCw, RotateCcw, Square, SquareStack, Trash2,
} from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { EmptyState, PageHeader, ProgressBar, StatusBadge, Surface } from '@/components/product/Primitives'
import { remoteApi, taskApi } from '@/lib/api'
import { useCapabilities } from '@/hooks/useProduct'
import { formatDate } from '@/lib/presentation'
import type { TaskAllowedAction, TaskSummary } from '@/types/product'

const PAGE_SIZE = 8
const statusFilters = [
  'all', 'running', 'queued', 'waiting_for_login', 'partial',
  'completed', 'failed', 'cancelled',
] as const
type TaskFilter = typeof statusFilters[number]

export default function TasksPage() {
  const { t, i18n } = useTranslation('product')
  const navigate = useNavigate()
  const capabilities = useCapabilities()
  const remote = Boolean(capabilities.data?.features.remote_worker)
  const [items, setItems] = useState<TaskSummary[]>([])
  const [filter, setFilter] = useState<TaskFilter>('all')
  const [page, setPage] = useState(1)
  const [total, setTotal] = useState(0)
  const [statusCounts, setStatusCounts] = useState<Record<string, number>>({})
  const [loading, setLoading] = useState(true)
  const [deleteTarget, setDeleteTarget] = useState<TaskSummary | null>(null)
  const [deleting, setDeleting] = useState(false)

  const load = useCallback(async (quiet = false) => {
    if (!capabilities.data) return
    if (!quiet) setLoading(true)
    try {
      const params = {
        limit: PAGE_SIZE,
        offset: (page - 1) * PAGE_SIZE,
        status: filter === 'all' ? undefined : filter,
      }
      const response = remote ? await remoteApi.runs(params) : await taskApi.list(params)
      setItems(response.data.items)
      setTotal(response.data.total)
      setStatusCounts(response.data.status_counts)
    } catch {
      if (!quiet) toast.error(t('tasks.operationRejected'))
    } finally {
      if (!quiet) setLoading(false)
    }
  }, [capabilities.data, filter, page, remote, t])

  useEffect(() => {
    void load()
    const timer = window.setInterval(() => void load(true), 5000)
    return () => window.clearInterval(timer)
  }, [load])

  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  useEffect(() => {
    if (page > pages) setPage(pages)
  }, [page, pages])

  const filterCount = (status: TaskFilter) => status === 'all'
    ? Object.values(statusCounts).reduce((sum, count) => sum + count, 0)
    : statusCounts[status] || 0

  const control = async (run: TaskSummary, action: TaskAllowedAction) => {
    if (action === 'rerun') {
      try {
        const response = remote ? await remoteApi.rerun(run.run_id) : await taskApi.rerun(run.run_id)
        toast.success(t('tasks.rerunCreated'))
        navigate(`/tasks/${response.data.run_id}`)
      } catch {
        toast.error(t('tasks.rerunRejected'))
      }
      return
    }
    if (action === 'view_results') { navigate('/library'); return }
    if (['view_failures', 'view_error', 'view_details'].includes(action)) { navigate(`/tasks/${run.run_id}`); return }
    if (action === 'reconnect') { navigate('/connect'); return }
    if (action === 'delete_history') {
      setDeleteTarget(run)
      return
    }
    if (action === 'cancel' && !window.confirm(t('tasks.cancelConfirm'))) return
    try {
      if (remote) {
        await remoteApi.control(run.run_id, action === 'retry_failed' ? 'retry-failed' : action as 'pause'|'resume'|'cancel')
      } else if (action === 'pause') await taskApi.pause(run.run_id)
      else if (action === 'resume') await taskApi.resume(run.run_id)
      else if (action === 'continue_after_login') await taskApi.continueAfterLogin(run.run_id)
      else if (action === 'cancel') await taskApi.cancel(run.run_id)
      else if (action === 'retry_failed') await taskApi.retry(run.run_id)
      toast.success(t('tasks.operationSubmitted'))
      await load(true)
    } catch {
      toast.error(t('tasks.operationRejected'))
    }
  }

  const deleteHistory = async () => {
    if (!deleteTarget) return
    setDeleting(true)
    try {
      if (remote) await remoteApi.removeRunHistory(deleteTarget.run_id)
      else await taskApi.removeHistory(deleteTarget.run_id)
      toast.success(t('tasks.deleteSuccess'))
      setDeleteTarget(null)
      await load(true)
    } catch {
      toast.error(t('tasks.deleteRejected'))
    } finally {
      setDeleting(false)
    }
  }

  return <div>
    <PageHeader
      eyebrow={t('tasks.eyebrow')}
      title={t('tasks.title')}
      description={t('tasks.description')}
      actions={<Button variant="outline" onClick={() => void load()} disabled={loading}><RefreshCw className={loading ? 'animate-spin' : ''}/>{t('tasks.refresh')}</Button>}
    />
    <div className="mb-4 flex gap-2 overflow-x-auto pb-1" role="tablist" aria-label={t('tasks.title')}>
      {statusFilters.map(status => <button
        key={status}
        type="button"
        role="tab"
        aria-selected={filter === status}
        onClick={() => { setFilter(status); setPage(1) }}
        className={`min-h-9 whitespace-nowrap rounded-full px-3 text-xs font-medium ring-1 ring-inset ${filter === status ? 'bg-slate-950 text-white ring-slate-950' : 'bg-white text-slate-600 ring-slate-200 hover:bg-slate-50'}`}
      >{t(`tasks.filters.${status}`)} {filterCount(status)}</button>)}
    </div>
    <Surface className="overflow-hidden">
      {items.length ? <>
        <div className="hidden grid-cols-[minmax(240px,1.55fr)_120px_190px_135px_minmax(170px,auto)] gap-4 border-b border-slate-200 bg-slate-50 px-5 py-2.5 text-xs font-medium text-slate-500 xl:grid">
          <span>{t('tasks.columns.task')}</span><span>{t('tasks.columns.status')}</span><span>{t('tasks.columns.progress')}</span><span>{t('tasks.columns.created')}</span><span className="text-right">{t('tasks.columns.actions')}</span>
        </div>
        <div>{items.map(run => <TaskRow key={run.run_id} run={run} locale={i18n.resolvedLanguage || i18n.language} onAction={control}/>)}</div>
        <div className="flex flex-col gap-3 border-t border-slate-200 bg-slate-50/70 px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
          <p className="text-center text-xs text-slate-500 sm:text-left">{t('tasks.pageSummary', { page, pages, total })}</p>
          <div className="flex justify-center gap-2">
            <Button size="sm" variant="outline" disabled={page <= 1 || loading} onClick={() => setPage(value => value - 1)}><ChevronLeft/>{t('tasks.previous')}</Button>
            <Button size="sm" variant="outline" disabled={page >= pages || loading} onClick={() => setPage(value => value + 1)}>{t('tasks.next')}<ChevronRight/></Button>
          </div>
        </div>
      </> : <EmptyState
        icon={<SquareStack/>}
        title={loading ? t('tasks.loadingTitle') : t('tasks.emptyTitle')}
        description={loading ? t('tasks.loadingDescription') : t('tasks.emptyDescription')}
        action={!loading ? <Button asChild><Link to="/crawl/new">{t('tasks.newCrawl')}</Link></Button> : undefined}
      />}
    </Surface>
    <Dialog open={Boolean(deleteTarget)} onOpenChange={open => { if (!open && !deleting) setDeleteTarget(null) }}><DialogContent className="border-slate-200 bg-white text-slate-900 sm:max-w-md"><DialogHeader><DialogTitle className="font-sans text-lg text-slate-950">{t('tasks.deleteTitle')}</DialogTitle><DialogDescription className="leading-6 text-slate-600">{t('tasks.deleteConfirm')}</DialogDescription></DialogHeader>{deleteTarget ? <div className="rounded-lg bg-slate-50 p-3 text-sm text-slate-700">{deleteTarget.display_name} · {t(`tasks.status.${deleteTarget.status}`)}</div> : null}<DialogFooter className="gap-2 sm:space-x-0"><Button variant="outline" disabled={deleting} onClick={() => setDeleteTarget(null)}>{t('tasks.deleteCancel')}</Button><Button variant="destructive" disabled={deleting} onClick={() => void deleteHistory()}>{deleting ? <RefreshCw className="animate-spin"/> : <Trash2/>}{deleting ? t('tasks.deleting') : t('tasks.deleteAction')}</Button></DialogFooter></DialogContent></Dialog>
  </div>
}

function TaskRow({ run, locale, onAction }: {
  run: TaskSummary
  locale: string
  onAction: (run: TaskSummary, action: TaskAllowedAction) => void
}) {
  const { t } = useTranslation('product')
  const primaryActions = run.allowed_actions.filter(action => !['continue_after_login', 'delete_history', 'view_details'].includes(action)).slice(0, 2)
  const canDelete = run.allowed_actions.includes('delete_history')
  const crawlerType = ['search', 'topic', 'detail', 'creator'].includes(run.crawler_type || '') ? run.crawler_type! : 'search'
  const displayName = run.source_missing
    ? t(`tasks.name.legacy_${crawlerType}`)
    : t(`tasks.name.${crawlerType}`, { value: run.source_summary })
  const accountLabel = run.connection_id ? run.account_label : t('tasks.localAccount')
  const progressLabel = run.progress.determinate
    ? t('tasks.workProgress', { completed: run.progress.completed, total: run.progress.total })
    : t('tasks.unknownProgress')
  const duration = formatTaskDuration(run.elapsed_seconds, t)
  const date = formatDate(run.created_at, locale)

  return <article className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-4 gap-y-3 border-b border-slate-100 p-4 last:border-0 sm:grid-cols-[minmax(220px,1fr)_110px_minmax(150px,auto)] xl:grid-cols-[minmax(240px,1.55fr)_120px_190px_135px_minmax(170px,auto)] xl:items-center xl:px-5 xl:py-3.5">
    <div className="min-w-0 xl:col-start-1 xl:row-start-1">
      <Link to={`/tasks/${run.run_id}`} className="group block min-w-0">
        <h2 className="truncate text-sm font-semibold text-slate-950 group-hover:text-teal-800">{displayName}</h2>
        <p className="mt-1 truncate text-xs text-slate-500">{accountLabel} · {t(`tasks.stage.${run.stage || 'discover'}`)}</p>
      </Link>
    </div>
    <div className="justify-self-end sm:justify-self-start xl:col-start-2 xl:row-start-1"><StatusBadge status={run.status} label={t(`tasks.status.${run.status}`)}/></div>
    <div className="col-span-2 sm:col-span-1 sm:col-start-1 sm:row-start-2 xl:col-start-3 xl:row-start-1">
      <div className="flex items-center justify-between gap-3 text-xs text-slate-500"><span className="truncate">{progressLabel}</span><span className="shrink-0 tabular-nums">{run.progress.determinate ? `${run.progress.percent}%` : t('tasks.unknownPercent')}</span></div>
      <div className="mt-2">{run.progress.determinate ? <ProgressBar value={run.progress.percent} label={progressLabel}/> : <div className="h-2 rounded-full bg-slate-100" aria-label={progressLabel}/>}</div>
    </div>
    <div className="col-span-2 text-xs text-slate-500 sm:col-span-1 sm:col-start-2 sm:row-start-2 xl:col-start-4 xl:row-start-1">
      <p>{date}</p><p className="mt-1 flex items-center gap-1"><Clock3 className="h-3.5 w-3.5"/><span>{duration}</span></p>
    </div>
    <div className="col-span-2 flex flex-wrap gap-2 sm:col-span-1 sm:col-start-3 sm:row-span-2 sm:row-start-1 sm:items-center sm:justify-end xl:col-start-5 xl:row-start-1">
      <Button asChild size="sm" variant="ghost"><Link to={`/tasks/${run.run_id}`}><Eye/>{t('tasks.action.view_details')}</Link></Button>
      {primaryActions.map(action => <Button key={action} size="sm" variant={action === 'cancel' ? 'ghost' : action === 'retry_failed' ? 'outline' : 'secondary'} onClick={() => onAction(run, action)}>
        {action === 'pause' ? <Pause/> : action === 'resume' ? <Play/> : action === 'cancel' ? <Square/> : action === 'retry_failed' || action === 'rerun' ? <RotateCcw/> : action === 'view_error' ? <AlertCircle/> : action === 'view_details' || action === 'view_failures' || action === 'view_results' ? <Eye/> : null}
        {t(`tasks.action.${action}`)}
      </Button>)}
      {canDelete ? <Button size="sm" variant="ghost" className="text-red-700 hover:bg-red-50 hover:text-red-800" onClick={() => onAction(run, 'delete_history')}><Trash2/><span className="xl:sr-only">{t('tasks.action.delete_history')}</span></Button> : null}
    </div>
  </article>
}

function formatTaskDuration(seconds: number | null | undefined, t: ReturnType<typeof useTranslation<'product'>>['t']) {
  if (seconds == null) return t('tasks.unknownDuration')
  if (seconds < 1) return t('tasks.lessThanSecond')
  if (seconds < 60) return t('tasks.seconds', { count: Math.round(seconds) })
  const minutes = Math.floor(seconds / 60)
  if (seconds < 3600) return t('tasks.minutes', { count: minutes })
  return t('tasks.hoursMinutes', { hours: Math.floor(minutes / 60), minutes: minutes % 60 })
}
