import { useEffect, useMemo, useState } from 'react'
import { Link, NavLink, Outlet, useLocation } from 'react-router-dom'
import {
  Activity, CalendarClock, ChevronRight, CircleUserRound, Clapperboard, FileSearch,
  Github, HeartPulse, Home, Library, Menu, MonitorCog, Plus, Settings, ShieldCheck,
  SquareStack, Users, X,
} from 'lucide-react'
import { systemApi } from '@/lib/api'
import type { Capabilities } from '@/types/product'
import { cn } from '@/lib/utils'
import { LanguageSwitch } from '@/components/layout/LanguageSwitch'
import { ThemeToggle } from '@/components/layout/ThemeToggle'

const userGroups = [
  { label: '工作台', items: [
    { to: '/', label: '首页', icon: Home, end: true },
    { to: '/crawl/new', label: '新建采集', icon: Plus },
    { to: '/tasks', label: '任务中心', icon: SquareStack },
  ] },
  { label: '数据', items: [
    { to: '/library', label: '内容库', icon: Library },
    { to: '/media', label: '媒体库', icon: Clapperboard },
  ] },
  { label: '自动化', items: [{ to: '/schedules', label: '定时计划', icon: CalendarClock }] },
  { label: '账号', items: [
    { to: '/connect', label: '抖音账号', icon: CircleUserRound },
    { to: '/settings', label: '设置', icon: Settings },
  ] },
]

const adminItems = [
  { to: '/admin/workers', label: '执行设备', icon: MonitorCog },
  { to: '/admin/verifications', label: '人工验证', icon: ShieldCheck },
  { to: '/admin/queue', label: '全局队列', icon: SquareStack },
  { to: '/admin/health', label: '系统健康', icon: HeartPulse },
]

function Brand() {
  return <Link to="/" className="flex min-h-16 items-center gap-3 border-b border-slate-200 px-5"><img src="/logos/flowlens-favicon.png" width="32" height="32" alt="" className="h-8 w-8 rounded-lg"/><div><div className="text-sm font-semibold tracking-tight text-slate-950">FlowLens</div><div className="text-[11px] text-slate-500">抖音数据工作台</div></div></Link>
}

function UserSidebar({ open, close }: { open: boolean; close: () => void }) {
  return <><button aria-label="关闭导航遮罩" className={cn('fixed inset-0 z-40 bg-slate-950/30 backdrop-blur-[1px] lg:hidden', open ? 'block' : 'hidden')} onClick={close}/><aside className={cn('fixed inset-y-0 left-0 z-50 flex w-[264px] flex-col border-r border-slate-200 bg-white transition-transform lg:static lg:translate-x-0', open ? 'translate-x-0' : '-translate-x-full')}>
    <div className="relative"><Brand/><button onClick={close} className="absolute right-3 top-3 grid h-10 w-10 place-items-center rounded-lg text-slate-500 hover:bg-slate-100 lg:hidden" aria-label="关闭导航"><X className="h-5 w-5"/></button></div>
    <nav className="flex-1 space-y-5 overflow-y-auto px-3 py-5">{userGroups.map(group => <div key={group.label}><p className="mb-1 px-3 text-[11px] font-semibold uppercase tracking-wider text-slate-400">{group.label}</p><div className="space-y-1">{group.items.map(item => <NavLink key={item.to} to={item.to} end={item.end} onClick={close} className={({ isActive }) => cn('flex min-h-10 items-center gap-3 rounded-lg px-3 text-sm font-medium transition-colors', isActive ? 'bg-teal-50 text-teal-800' : 'text-slate-600 hover:bg-slate-50 hover:text-slate-950')}><item.icon className="h-[18px] w-[18px]"/><span>{item.label}</span></NavLink>)}</div></div>)}</nav>
    <div className="border-t border-slate-200 p-4"><a href="https://github.com/simonyi29/flowlens" target="_blank" rel="noreferrer" className="flex items-center justify-between rounded-lg px-2 py-2 text-xs text-slate-500 hover:bg-slate-50 hover:text-slate-900"><span className="flex items-center gap-2"><Github className="h-4 w-4"/>GitHub</span><ChevronRight className="h-4 w-4"/></a></div>
  </aside></>
}

function TopBar({ openMenu, onShowDisclaimer, capabilities }: { openMenu: () => void; onShowDisclaimer: () => void; capabilities: Capabilities | null }) {
  const location = useLocation()
  const title = useMemo(() => {
    const item = userGroups.flatMap(group => group.items).find(nav => nav.to !== '/' ? location.pathname.startsWith(nav.to) : location.pathname === '/')
    return item?.label ?? 'FlowLens'
  }, [location.pathname])
  return <header className="sticky top-0 z-30 flex h-16 items-center justify-between border-b border-slate-200 bg-white/95 px-2 backdrop-blur sm:px-6 lg:px-8"><div className="flex min-w-0 items-center gap-2 sm:gap-3"><button onClick={openMenu} className="grid h-11 w-11 shrink-0 place-items-center rounded-lg text-slate-600 hover:bg-slate-100 lg:hidden" aria-label="打开导航"><Menu className="h-5 w-5"/></button><div className="min-w-0"><p className="truncate text-sm font-semibold text-slate-900">{title}</p><p className="hidden text-xs text-slate-500 sm:block">公开数据采集、整理与本地分析</p></div></div><div className="flex shrink-0 items-center gap-1 sm:gap-2"><button onClick={onShowDisclaimer} className="hidden min-h-8 items-center rounded-full bg-amber-50 px-3 text-xs font-medium text-amber-800 ring-1 ring-inset ring-amber-200 sm:inline-flex">非商业研究用途</button>{capabilities?.features.admin ? <Link to="/admin/workers" className="hidden min-h-9 items-center gap-2 rounded-lg px-3 text-xs font-medium text-slate-600 hover:bg-slate-100 md:flex"><ShieldCheck className="h-4 w-4"/>管理员</Link> : null}<ThemeToggle/><LanguageSwitch/></div></header>
}

function MobileNavigation() {
  const items = [
    { to: '/', label: '首页', icon: Home, end: true },
    { to: '/tasks', label: '任务', icon: SquareStack },
    { to: '/library', label: '内容', icon: FileSearch },
    { to: '/connect', label: '账号', icon: CircleUserRound },
  ]
  return <nav className="fixed inset-x-0 bottom-0 z-30 grid h-16 grid-cols-4 border-t border-slate-200 bg-white/95 px-2 pb-[env(safe-area-inset-bottom)] backdrop-blur lg:hidden">{items.map(item => <NavLink key={item.to} to={item.to} end={item.end} className={({ isActive }) => cn('flex flex-col items-center justify-center gap-1 text-[11px] font-medium', isActive ? 'text-teal-700' : 'text-slate-500')}><item.icon className="h-5 w-5"/><span>{item.label}</span></NavLink>)}</nav>
}

export function UserAppShell({ onShowDisclaimer }: { onShowDisclaimer: () => void }) {
  const [menuOpen, setMenuOpen] = useState(false)
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null)
  useEffect(() => { systemApi.capabilities().then(response => setCapabilities(response.data)).catch(() => setCapabilities(null)) }, [])
  return <div className="flex min-h-screen bg-app-canvas text-slate-900"><a href="#main-content" className="fixed left-3 top-3 z-[100] -translate-y-20 rounded-lg bg-slate-950 px-4 py-2 text-sm text-white focus:translate-y-0">跳到主要内容</a><UserSidebar open={menuOpen} close={() => setMenuOpen(false)}/><div className="min-w-0 flex-1"><TopBar openMenu={() => setMenuOpen(true)} onShowDisclaimer={onShowDisclaimer} capabilities={capabilities}/><main id="main-content" className="mx-auto w-full max-w-[1560px] px-4 pb-24 pt-6 sm:px-6 lg:px-8 lg:pb-8"><Outlet context={{ capabilities }}/></main><footer className="hidden border-t border-slate-200 bg-white px-8 py-3 text-xs text-slate-500 lg:flex lg:items-center lg:justify-between"><span>FlowLens 1.2 · 非商业学习与研究用途</span><a href="https://github.com/simonyi29/flowlens" target="_blank" rel="noreferrer" className="hover:text-teal-700">GitHub 仓库</a></footer></div><MobileNavigation/></div>
}

export function AdminAppShell() {
  const [open, setOpen] = useState(false)
  return <div className="flex min-h-screen bg-slate-100 text-slate-900"><button aria-label="关闭管理员导航遮罩" className={cn('fixed inset-0 z-40 bg-slate-950/40 lg:hidden', open ? 'block' : 'hidden')} onClick={() => setOpen(false)}/><aside className={cn('fixed inset-y-0 left-0 z-50 flex w-[264px] flex-col bg-slate-950 text-slate-200 transition-transform lg:static lg:translate-x-0', open ? 'translate-x-0' : '-translate-x-full')}><Link to="/" className="flex h-16 items-center gap-3 border-b border-white/10 px-5"><img src="/logos/flowlens-favicon.png" width="32" height="32" className="h-8 w-8 rounded-lg" alt=""/><div><div className="text-sm font-semibold text-white">FlowLens 管理台</div><div className="text-[11px] text-slate-400">设备与验证运维</div></div></Link><nav className="flex-1 space-y-1 p-3">{adminItems.map(item => <NavLink key={item.to} to={item.to} onClick={() => setOpen(false)} className={({ isActive }) => cn('flex min-h-11 items-center gap-3 rounded-lg px-3 text-sm font-medium', isActive ? 'bg-white/10 text-white' : 'text-slate-400 hover:bg-white/5 hover:text-white')}><item.icon className="h-[18px] w-[18px]"/>{item.label}</NavLink>)}</nav><Link to="/" className="m-3 flex min-h-11 items-center gap-2 rounded-lg border border-white/10 px-3 text-sm text-slate-300 hover:bg-white/5"><Users className="h-4 w-4"/>返回用户端</Link></aside><div className="min-w-0 flex-1"><header className="flex h-16 items-center justify-between border-b border-slate-200 bg-white px-4 sm:px-6"><button aria-label="打开管理员导航" className="grid h-11 w-11 place-items-center rounded-lg lg:hidden" onClick={() => setOpen(true)}><Menu className="h-5 w-5"/></button><div><p className="text-sm font-semibold">管理员后台</p><p className="text-xs text-slate-500">敏感认证信息不会在此处显示</p></div><span className="hidden items-center gap-2 text-xs text-slate-500 sm:flex"><Activity className="h-4 w-4 text-emerald-600"/>本机控制服务</span></header><main className="p-4 sm:p-6 lg:p-8"><Outlet/></main></div></div>
}
