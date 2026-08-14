import { useState, type FormEvent } from 'react'
import { Navigate, useNavigate } from 'react-router-dom'
import { KeyRound, Loader2, ShieldCheck } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { useAuth } from '@/contexts/AuthContext'

export default function LoginPage(){
  const{user,loading,remote,login}=useAuth();const navigate=useNavigate();const[username,setUsername]=useState('');const[password,setPassword]=useState('');const[busy,setBusy]=useState(false)
  if(loading)return null
  if(!remote)return <Navigate to="/" replace/>
  if(user)return <Navigate to={user.must_change_password?'/change-password':'/'} replace/>
  const submit=async(event:FormEvent)=>{event.preventDefault();setBusy(true);try{const next=await login(username,password);navigate(next.must_change_password?'/change-password':'/',{replace:true})}catch(error:any){toast.error(error?.response?.data?.detail?.user_message||'用户名或密码错误')}finally{setBusy(false)}}
  return <div className="grid min-h-screen place-items-center bg-slate-100 px-4"><main className="w-full max-w-md rounded-2xl border border-slate-200 bg-white p-7 shadow-sm sm:p-9"><img src="/logos/flowlens-favicon.png" alt="" className="h-11 w-11 rounded-xl"/><p className="mt-5 text-sm font-semibold text-teal-700">FlowLens 1.3</p><h1 className="mt-1 text-2xl font-semibold tracking-tight text-slate-950">登录数据工作台</h1><p className="mt-2 text-sm leading-6 text-slate-600">账号由管理员创建，暂不开放公众注册。</p><form onSubmit={submit} className="mt-7 space-y-5"><label className="block text-sm font-medium text-slate-700">用户名<Input autoFocus autoComplete="username" value={username} onChange={e=>setUsername(e.target.value)} className="mt-2 h-11"/></label><label className="block text-sm font-medium text-slate-700">密码<Input type="password" autoComplete="current-password" value={password} onChange={e=>setPassword(e.target.value)} className="mt-2 h-11"/></label><Button className="h-11 w-full" disabled={busy||!username||!password}>{busy?<Loader2 className="animate-spin"/>:<KeyRound/>}登录</Button></form><div className="mt-6 flex gap-3 rounded-xl bg-slate-50 p-4 text-xs leading-5 text-slate-600"><ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-teal-700"/><span>浏览器只保存 HttpOnly 会话 Cookie，不接触抖音 Cookie、Chrome Profile 或设备密钥。</span></div></main></div>
}
