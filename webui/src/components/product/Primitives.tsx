import type { ReactNode } from 'react'
import { AlertCircle, Inbox, Loader2 } from 'lucide-react'
import { cn } from '@/lib/utils'
import { statusPresentation } from '@/lib/presentation'

export function PageLoader() {
  return <div className="grid min-h-screen place-items-center bg-app-canvas"><div className="flex items-center gap-3 text-sm text-slate-500"><Loader2 className="h-5 w-5 animate-spin text-teal-700"/>正在加载 FlowLens…</div></div>
}

export function PageHeader({ eyebrow, title, description, actions }: { eyebrow?: string; title: string; description?: string; actions?: ReactNode }) {
  return <header className="mb-6 flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
    <div><p className="mb-1 text-xs font-semibold uppercase tracking-[0.12em] text-teal-700">{eyebrow}</p><h1 className="text-2xl font-semibold tracking-tight text-slate-950 sm:text-[28px]">{title}</h1>{description ? <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-600">{description}</p> : null}</div>{actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
  </header>
}

export function Surface({ className, children }: { className?: string; children: ReactNode }) {
  return <section className={cn('rounded-xl border border-slate-200 bg-white shadow-[0_1px_2px_rgba(15,23,42,.04)]', className)}>{children}</section>
}

export function StatusBadge({ status, label }: { status: string; label?: string }) {
  const item = statusPresentation[status] ?? { label: status, tone: 'neutral' }
  return <span className={cn('inline-flex min-h-6 items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium', {
    'bg-emerald-50 text-emerald-700 ring-1 ring-inset ring-emerald-200': item.tone === 'success',
    'bg-sky-50 text-sky-700 ring-1 ring-inset ring-sky-200': item.tone === 'info',
    'bg-amber-50 text-amber-800 ring-1 ring-inset ring-amber-200': item.tone === 'warning',
    'bg-red-50 text-red-700 ring-1 ring-inset ring-red-200': item.tone === 'danger',
    'bg-slate-100 text-slate-600 ring-1 ring-inset ring-slate-200': item.tone === 'neutral',
  })}><span className="h-1.5 w-1.5 rounded-full bg-current" aria-hidden="true"/>{label ?? item.label}</span>
}

export function EmptyState({ title, description, action, icon = <Inbox/> }: { title: string; description: string; action?: ReactNode; icon?: ReactNode }) {
  return <div className="flex min-h-64 flex-col items-center justify-center px-6 py-12 text-center"><div className="mb-4 grid h-11 w-11 place-items-center rounded-xl bg-slate-100 text-slate-500 [&_svg]:h-5 [&_svg]:w-5">{icon}</div><h3 className="text-sm font-semibold text-slate-900">{title}</h3><p className="mt-1 max-w-sm text-sm leading-6 text-slate-500">{description}</p>{action ? <div className="mt-5">{action}</div> : null}</div>
}

export function ErrorState({ title = '加载失败', description, retry }: { title?: string; description: string; retry?: () => void }) {
  return <div className="rounded-xl border border-red-200 bg-red-50 p-4 text-red-800"><div className="flex gap-3"><AlertCircle className="mt-0.5 h-5 w-5 shrink-0"/><div><p className="font-medium">{title}</p><p className="mt-1 text-sm text-red-700">{description}</p>{retry ? <button className="mt-3 text-sm font-semibold underline underline-offset-4" onClick={retry}>重新加载</button> : null}</div></div></div>
}

export function ProgressBar({ value, label }: { value: number; label?: string }) {
  const safe = Math.max(0, Math.min(value, 100))
  return <div><div className="h-2 overflow-hidden rounded-full bg-slate-100" role="progressbar" aria-label={label ?? '任务进度'} aria-valuemin={0} aria-valuemax={100} aria-valuenow={safe}><div className="h-full rounded-full bg-teal-700 transition-[width]" style={{ width: `${safe}%` }}/></div><span className="sr-only">{safe}%</span></div>
}
