import { useEffect, useState } from 'react'
import { libraryApi, mediaApi, scheduleApi, systemApi, taskApi } from '@/lib/api'
import type { Aweme, MediaAsset, TaskRun } from '@/types/crawler'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'

const panel = 'glass-panel rounded-xl border border-cyber-border-subtle p-4 overflow-auto'

export function TaskCenter() {
  const [items,setItems]=useState<TaskRun[]>([]),[detail,setDetail]=useState<Record<string,unknown>|null>(null)
  const load=()=>taskApi.list().then(r=>setItems(r.data.items))
  const inspect=(id:string)=>Promise.all([taskApi.items(id),taskApi.logs(id)]).then(([a,b])=>setDetail({stages:a.data.stages,items:a.data.items,logs:b.data.logs}))
  useEffect(()=>{load();const id=setInterval(load,3000);return()=>clearInterval(id)},[])
  return <div className={panel}><h2 className="font-mono text-cyber-neon-cyan mb-3">任务中心</h2>
    <div className="space-y-2">{items.map(x=><div key={x.run_id} className="border border-cyber-border-subtle rounded-lg p-3 flex justify-between gap-3">
      <button className="text-left" onClick={()=>inspect(x.run_id)}><div className="font-mono text-sm">{x.crawler_type} · {x.stage}</div><div className="text-xs text-cyber-text-muted">{x.run_id} · {x.status}</div></button>
      <div className="flex gap-2"><Button size="sm" onClick={()=>taskApi.pause(x.run_id).then(load)}>暂停</Button><Button size="sm" onClick={()=>taskApi.resume(x.run_id).then(load)}>继续</Button><Button size="sm" onClick={()=>taskApi.retry(x.run_id).then(load)}>重试</Button><Button size="sm" variant="destructive" onClick={()=>taskApi.cancel(x.run_id).then(load)}>取消</Button></div>
    </div>)}</div>{detail?<pre className="mt-4 text-xs whitespace-pre-wrap bg-black/20 p-3 rounded-lg max-h-80 overflow-auto">{JSON.stringify(detail,null,2)}</pre>:null}</div>
}

export function ContentLibrary() {
  const [items,setItems]=useState<Aweme[]>([]),[q,setQ]=useState(''),[selected,setSelected]=useState<Record<string,unknown>|null>(null)
  const load=()=>libraryApi.awemes(q).then(r=>setItems(r.data.items))
  useEffect(()=>{void load()},[])
  return <div className={`${panel} space-y-3`}><h2 className="font-mono text-cyber-neon-cyan">内容库</h2><div className="flex gap-2"><Input value={q} onChange={e=>setQ(e.target.value)} placeholder="搜索文案或标题"/><Button onClick={load}>搜索</Button></div>
    <div className="grid md:grid-cols-2 gap-2">{items.map(x=><button key={x.aweme_id} onClick={()=>libraryApi.detail(x.aweme_id).then(r=>setSelected(r.data))} className="text-left border border-cyber-border-subtle p-3 rounded-lg"><div className="line-clamp-2 text-sm">{x.title}</div><div className="text-xs text-cyber-text-muted mt-1">赞 {x.liked_count??'-'} · 评 {x.comment_count??'-'}</div></button>)}</div>
    {selected?<pre className="text-xs whitespace-pre-wrap bg-black/20 p-3 rounded-lg">{JSON.stringify(selected,null,2)}</pre>:null}</div>
}

export function MediaLibrary() {
  const [items,setItems]=useState<MediaAsset[]>([]);const load=()=>mediaApi.list().then(r=>setItems(r.data.items));useEffect(()=>{void load()},[])
  return <div className={panel}><h2 className="font-mono text-cyber-neon-cyan mb-3">媒体库</h2><div className="grid md:grid-cols-2 gap-3">{items.map(x=><div key={x.asset_id} className="border border-cyber-border-subtle rounded-lg p-3"><div>{x.aweme_id} · {x.kind}</div><div className="text-xs text-cyber-text-muted">{x.status} · {(x.size_bytes/1048576).toFixed(1)} MB</div>{x.status==='completed'?<video className="w-full mt-2" controls src={mediaApi.streamUrl(x.asset_id)}/>:null}<Button className="mt-2" size="sm" variant="destructive" onClick={()=>confirm('确认删除这个正式媒体文件？')&&mediaApi.remove(x.asset_id).then(load)}>删除</Button></div>)}</div></div>
}

export function Schedules() {
  const [items,setItems]=useState<Record<string,unknown>[]>([]),[name,setName]=useState('每日增量'),[source,setSource]=useState(''),[mode,setMode]=useState<'creator'|'topic'>('creator'),[interval,setIntervalType]=useState<'once'|'hourly'|'daily'>('daily'),[runAt,setRunAt]=useState('');const load=()=>scheduleApi.list().then(r=>setItems(r.data.items));useEffect(()=>{void load()},[])
  const payload=(enabled=true)=>({name,enabled,platform:'dy',crawler_type:mode,source,interval_type:interval,interval_value:1,run_at:interval==='once'?new Date(runAt).toISOString():null,timezone:'Asia/Shanghai',config:{platform:'dy',crawler_type:mode,creator_ids:mode==='creator'?source:'',topics:mode==='topic'?source:'',incremental:true,save_option:'jsonl'}})
  const create=()=>scheduleApi.create(payload()).then(()=>{setSource('');return load()})
  return <div className={`${panel} space-y-3`}><h2 className="font-mono text-cyber-neon-cyan">定时计划</h2><div className="grid md:grid-cols-6 gap-2"><Input value={name} onChange={e=>setName(e.target.value)} placeholder="计划名称"/><select className="bg-cyber-bg-tertiary border border-cyber-border-subtle rounded-md px-2 text-xs" value={mode} onChange={e=>setMode(e.target.value as 'creator'|'topic')}><option value="creator">账号</option><option value="topic">话题</option></select><select className="bg-cyber-bg-tertiary border border-cyber-border-subtle rounded-md px-2 text-xs" value={interval} onChange={e=>setIntervalType(e.target.value as 'once'|'hourly'|'daily')}><option value="once">单次</option><option value="hourly">每小时</option><option value="daily">每天</option></select><Input value={source} onChange={e=>setSource(e.target.value)} placeholder="账号 URL / 话题"/>{interval==='once'?<Input type="datetime-local" value={runAt} onChange={e=>setRunAt(e.target.value)}/>:<span/>}<Button disabled={!source.trim()||(interval==='once'&&!runAt)} onClick={create}>创建计划</Button></div>{items.map(x=><div key={String(x.schedule_id)} className="border border-cyber-border-subtle rounded-lg p-3 flex justify-between"><span>{String(x.name)} · {String(x.interval_type)} · {String(x.enabled)==='1'?'启用':'停用'} · {String(x.next_run_at??'已结束')}</span><div className="flex gap-2"><Button size="sm" onClick={()=>scheduleApi.run(String(x.schedule_id)).then(load)}>立即运行</Button><Button size="sm" onClick={()=>scheduleApi.update(String(x.schedule_id),{...payload(!(Number(x.enabled))),name:String(x.name),source:String(x.source),crawler_type:String(x.crawler_type),interval_type:String(x.interval_type),run_at:x.run_at||null}).then(load)}>{Number(x.enabled)?'停用':'启用'}</Button><Button size="sm" variant="destructive" onClick={()=>confirm('删除计划不会删除历史任务，确认继续？')&&scheduleApi.remove(String(x.schedule_id)).then(load)}>删除</Button></div></div>)}</div>
}

export function HealthPage(){const [data,setData]=useState<Record<string,unknown>|null>(null);useEffect(()=>{Promise.all([systemApi.health(),systemApi.storage()]).then(([h,s])=>setData({health:h.data,storage:s.data}))},[]);return <div className={panel}><h2 className="font-mono text-cyber-neon-cyan mb-3">系统健康</h2><pre className="text-xs whitespace-pre-wrap">{JSON.stringify(data,null,2)}</pre></div>}
