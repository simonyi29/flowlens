import { useEffect, useState } from 'react'
import { AlertTriangle, CheckCircle2, Clock3, Link2, Loader2, Monitor, Pencil, Plus, RefreshCw, ShieldCheck, Smartphone, Unlink } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { EmptyState, PageHeader, StatusBadge, Surface } from '@/components/product/Primitives'
import { remoteApi, systemApi } from '@/lib/api'
import { useCapabilities } from '@/hooks/useProduct'
import { formatDate } from '@/lib/presentation'
import { useAuth } from '@/contexts/AuthContext'

type Item = Record<string, unknown>

const loginText: Record<string, string> = {
  queued: '正在排队等待执行设备', starting_browser: '正在启动安全浏览器', opening_login_page: '正在打开抖音登录页',
  generating_qr: '正在生成二维码', qr_ready: '请使用抖音 App 扫码', qr_scanned: '已扫码，请在手机中确认',
  phone_confirmation_required: '等待手机确认', checking_login: '正在确认登录状态', logged_in: '账号连接成功',
  captcha_required: '需要管理员处理验证', risk_controlled: '当前会话触发风险提示', expired: '二维码已过期',
  cancelled: '连接已取消', failed: '连接失败',
}

export default function ConnectionPage() {
  const capabilities = useCapabilities()
  const remote = capabilities.data?.features.remote_worker
  if (capabilities.isLoading) return <ConnectionSkeleton/>
  return remote ? <RemoteConnection/> : <LocalConnection/>
}

function ConnectionSkeleton() {
  return <div><PageHeader eyebrow="账号" title="抖音账号" description="正在读取账号连接能力…"/><Surface className="h-72 animate-pulse bg-slate-100"><span className="sr-only">正在加载</span></Surface></div>
}

function LocalConnection() {
  const [health, setHealth] = useState<Item | null>(null)
  const load = () => systemApi.health().then(response => setHealth(response.data)).catch(() => setHealth(null))
  useEffect(() => { void load() }, [])
  const browserCheck = ((health?.checks as Item | undefined)?.cdp || {}) as Item
  const connected = Boolean(browserCheck.ok)
  return <div><PageHeader eyebrow="账号" title="抖音账号" description="当前为本机模式。FlowLens 会连接这台电脑上的 Chrome，不会保存 Cookie 到任务配置。" actions={<Button variant="outline" onClick={load}><RefreshCw/>检查登录</Button>}/><Surface className="overflow-hidden"><div className="p-6 sm:p-8"><div className="flex flex-col gap-6 sm:flex-row sm:items-center sm:justify-between"><div className="flex items-start gap-4"><div className="grid h-12 w-12 place-items-center rounded-xl bg-teal-50 text-teal-700"><Monitor className="h-6 w-6"/></div><div><div className="flex items-center gap-2"><h2 className="text-lg font-semibold text-slate-950">本机 Chrome</h2><StatusBadge status={connected ? 'connected' : 'offline'} label={connected ? '浏览器可用' : '尚未连接'}/></div><p className="mt-2 max-w-xl text-sm leading-6 text-slate-600">{connected ? '已检测到可用的本机浏览器会话。请确认 Chrome 中已经登录抖音，再开始采集。' : '请从设置中的本机高级工具启动 Chrome，并在浏览器中完成抖音登录。'}</p></div></div><Button asChild><a href="#/settings">打开本机高级工具</a></Button></div></div><div className="grid border-t border-slate-200 sm:grid-cols-3"><Info label="会话位置" value="仅保存在本机浏览器"/><Info label="登录状态" value={String(browserCheck.login_state || '等待检查')}/><Info label="安全限制" value="仅允许本机访问"/></div></Surface></div>
}

function RemoteConnection() {
  const { user } = useAuth()
  const [workers, setWorkers] = useState<Item[]>([])
  const [connections, setConnections] = useState<Item[]>([])
  const [session, setSession] = useState<Item | null>(null)
  const [qrUrl, setQrUrl] = useState('')
  const [busy, setBusy] = useState(false)
  const [now, setNow] = useState(0)
  const onlineWorker = workers.some(item => item.status === 'online')
  const activeConnections = connections.filter(item => item.status !== 'disconnected')
  const connectionLimit = user?.max_douyin_connections || 3
  const load = async () => {
    const [workerResponse, connectionResponse] = await Promise.all([remoteApi.workers(), remoteApi.connections()])
    setWorkers(workerResponse.data.items); setConnections(connectionResponse.data.items)
  }
  useEffect(() => { void load().catch(() => undefined); const timer = setInterval(() => load().catch(() => undefined), 5000); return () => clearInterval(timer) }, [])
  useEffect(() => {
    if (!session?.login_session_id) return
    const timer = setInterval(async () => {
      const response = await remoteApi.login(String(session.login_session_id)); setSession(response.data)
      if (response.data.qr_available && !qrUrl) {
        const image = await remoteApi.qr(String(session.login_session_id)); setQrUrl(URL.createObjectURL(image.data))
      }
      if (['logged_in','expired','cancelled','failed','captcha_required','risk_controlled'].includes(response.data.status)) void load()
    }, 1000)
    return () => clearInterval(timer)
  }, [session?.login_session_id, qrUrl])
  useEffect(() => () => { if (qrUrl) URL.revokeObjectURL(qrUrl) }, [qrUrl])
  useEffect(() => { const update = () => setNow(Date.now()); update(); const timer = setInterval(update, 1000); return () => clearInterval(timer) }, [])
  const start = async () => {
    if (!onlineWorker) return
    setBusy(true)
    try { const response = await remoteApi.createLogin(); setQrUrl(''); setSession(response.data) }
    catch (error:any) { toast.error(error?.response?.data?.detail?.user_message || '无法创建登录会话，请确认执行设备在线或账号配额') } finally { setBusy(false) }
  }
  const refresh = async () => {
    if (!session) return
    await remoteApi.refreshLogin(String(session.login_session_id)); if (qrUrl) URL.revokeObjectURL(qrUrl); setQrUrl(''); setSession({ ...session, status: 'queued' })
  }
  const reconnect = async (item:Item) => {
    const response = await remoteApi.reconnect(String(item.connection_id)); setQrUrl(''); setSession(response.data)
  }
  const disconnect = async (item:Item) => {
    if (!confirm(`断开“${String(item.display_name || item.remark || item.masked_nickname || '抖音账号')}”后会删除它的专用浏览器会话，历史采集结果不会删除。确认继续？`)) return
    await remoteApi.disconnect(String(item.connection_id)); toast.success('账号已断开'); await load()
  }
  const edit = async (item:Item) => {
    const value = prompt('输入这个账号的用户可见名称', String(item.display_name || item.remark || item.masked_nickname || ''))
    if (value === null || !value.trim()) return
    await remoteApi.updateConnection(String(item.connection_id), {display_name:value.trim()}); toast.success('账号名称已更新'); await load()
  }
  const liveSession = session && !['logged_in','cancelled','failed'].includes(String(session.status))
  const expiresAt = session?.expires_at && now ? Math.max(0, Math.ceil((new Date(String(session.expires_at)).getTime() - now) / 1000)) : 0
  return <div><PageHeader eyebrow="账号" title="抖音账号" description="每个抖音账号使用独立 Chrome Profile。Cookie、浏览器会话和技术参数不会上传到网站。" actions={<Button disabled={!onlineWorker||busy||Boolean(liveSession)||activeConnections.length>=connectionLimit} onClick={start}>{busy?<Loader2 className="animate-spin"/>:<Plus/>}连接新账号</Button>}/>
    <div className="mb-4 grid gap-3 sm:grid-cols-3"><InfoTile label="账号配额" value={`${activeConnections.length} / ${connectionLimit}`}/><InfoTile label="已连接" value={String(activeConnections.filter(item=>item.status==='connected').length)}/><InfoTile label="需要重新登录" value={String(activeConnections.filter(item=>['session_expired','verification_required','risk_controlled'].includes(String(item.status))).length)}/></div>
    {liveSession ? <Surface className="mx-auto mb-4 max-w-3xl overflow-hidden"><div className="grid md:grid-cols-[320px_1fr]"><div className="grid min-h-80 place-items-center bg-slate-50 p-8"><div className="relative aspect-square w-full max-w-[240px] rounded-2xl bg-white p-3 shadow-sm ring-1 ring-slate-200">{qrUrl ? <img src={qrUrl} alt="抖音登录二维码" className="h-full w-full object-contain"/> : <div className="grid h-full place-items-center"><Loader2 className="h-8 w-8 animate-spin text-teal-700"/></div>}</div></div><div className="flex flex-col justify-center p-6 sm:p-8"><div className="flex items-center gap-2 text-xs font-medium text-slate-500"><Clock3 className="h-4 w-4"/>二维码有效期约 {expiresAt || 180} 秒</div><h2 className="mt-4 text-xl font-semibold text-slate-950">{loginText[String(session.status)] || String(session.status)}</h2><p className="mt-2 text-sm leading-6 text-slate-600">打开抖音 App 扫描二维码，并按手机提示确认。登录成功后二维码会立即失效。</p>{['captcha_required','risk_controlled'].includes(String(session.status)) ? <div className="mt-5 flex gap-3 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900"><AlertTriangle className="mt-0.5 h-4 w-4 shrink-0"/><span>需要管理员在执行设备处理验证，系统不会绕过验证码。</span></div> : null}<div className="mt-6 flex gap-2"><Button variant="outline" onClick={refresh}><RefreshCw/>刷新二维码</Button><Button variant="ghost" onClick={() => remoteApi.cancelLogin(String(session.login_session_id)).then(() => { setSession(null); setQrUrl('') })}>取消</Button></div></div></div></Surface> : null}
    {!activeConnections.length && !liveSession ? <Surface><EmptyState icon={onlineWorker?<Smartphone/>:<AlertTriangle/>} title={onlineWorker?'尚未连接抖音账号':'执行设备离线'} description={onlineWorker?'连接后可选择具体账号执行公开数据采集。每个账号都拥有独立浏览器会话。':'当前没有可用执行设备，请联系管理员。'} action={<Button size="lg" disabled={!onlineWorker||busy} onClick={start}><Link2/>连接抖音账号</Button>}/></Surface> : <div className="grid gap-4 xl:grid-cols-2">{activeConnections.map(item=>{const worker=workers.find(value=>value.worker_id===item.worker_id);const needsLogin=['session_expired','verification_required','risk_controlled'].includes(String(item.status));return <Surface key={String(item.connection_id)} className="overflow-hidden"><div className="p-5 sm:p-6"><div className="flex items-start justify-between gap-4"><div className="flex min-w-0 gap-3"><div className="grid h-11 w-11 shrink-0 place-items-center rounded-xl bg-teal-50 text-teal-700"><CheckCircle2 className="h-5 w-5"/></div><div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><h2 className="truncate font-semibold text-slate-950">{String(item.display_name||item.remark||item.masked_nickname||'抖音账号')}</h2><StatusBadge status={String(item.status)}/></div><p className="mt-1 truncate text-xs text-slate-500">{item.display_name&&item.masked_nickname?String(item.masked_nickname):'独立浏览器会话'}</p></div></div><Button size="sm" variant="ghost" onClick={()=>edit(item)}><Pencil/>编辑</Button></div><div className="mt-5 grid grid-cols-2 gap-3 text-xs"><div><p className="text-slate-500">执行设备</p><p className="mt-1 font-medium">{String(worker?.name||'未分配')}</p></div><div><p className="text-slate-500">最后验证</p><p className="mt-1 font-medium">{formatDate(String(item.last_verified_at||''))}</p></div></div><div className="mt-5 flex flex-wrap gap-2"><Button size="sm" variant="outline" onClick={load}><RefreshCw/>检查状态</Button>{needsLogin?<Button size="sm" onClick={()=>reconnect(item)}>重新扫码</Button>:null}<Button size="sm" variant="ghost" className="text-red-700" onClick={()=>disconnect(item)}><Unlink/>断开</Button></div></div></Surface>})}</div>}
    <div className="mt-4 flex items-start gap-3 rounded-xl border border-slate-200 bg-white p-4 text-sm text-slate-600"><ShieldCheck className="mt-0.5 h-5 w-5 shrink-0 text-teal-700"/><p>每个网站用户使用独立的抖音浏览器会话。普通用户不会接触抓取机器、浏览器技术参数或登录凭据。</p></div>
  </div>
}

function InfoTile({label,value}:{label:string;value:string}){return <Surface className="p-4"><p className="text-xs text-slate-500">{label}</p><p className="mt-1 text-xl font-semibold text-slate-950">{value}</p></Surface>}

function Info({ label, value }: { label: string; value: string }) {
  return <div className="border-b border-slate-200 px-6 py-4 last:border-b-0 sm:border-b-0 sm:border-r sm:last:border-r-0"><p className="text-xs text-slate-500">{label}</p><p className="mt-1 truncate text-sm font-medium text-slate-900">{value}</p></div>
}
