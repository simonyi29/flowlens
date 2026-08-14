import { useEffect, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Copy,
  HardDrive,
  KeyRound,
  MonitorCog,
  RefreshCw,
  ShieldAlert,
  SquareStack,
} from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  EmptyState,
  PageHeader,
  StatusBadge,
  Surface,
} from "@/components/product/Primitives";
import { remoteApi, systemApi } from "@/lib/api";
import { formatBytes, formatDate } from "@/lib/presentation";

export default function AdminPage({
  section,
}: {
  section: "workers" | "verifications" | "queue" | "health";
}) {
  if (section === "workers") return <Workers />;
  if (section === "verifications") return <Verifications />;
  if (section === "queue") return <Queue />;
  return <Health />;
}
function Workers() {
  const [items, setItems] = useState<Record<string, unknown>[]>([]),
    [code, setCode] = useState("");
  const load = () =>
    remoteApi
      .adminWorkers()
      .then((r) => setItems(r.data.items))
      .catch(() => setItems([]));
  useEffect(() => {
    void load();
  }, []);
  return (
    <div>
      <PageHeader
        eyebrow="管理员"
        title="执行设备"
        description="查看 Worker 在线状态、版本和能力。认证材料不会显示在页面中。"
        actions={
          <Button variant="outline" onClick={load}>
            <RefreshCw />
            刷新
          </Button>
        }
      />
      <div className="mb-4">
        <Button
          onClick={() =>
            remoteApi
              .enrollment()
              .then((r) => setCode(r.data.enrollment_code))
              .catch(() => toast.error("远程模式未启用或没有管理员权限"))
          }
        >
          <KeyRound />
          生成 10 分钟注册码
        </Button>
        {code ? (
          <div className="mt-3 flex items-center gap-3 rounded-xl border border-amber-200 bg-amber-50 p-4">
            <code className="min-w-0 flex-1 break-all text-xs text-amber-900">
              {code}
            </code>
            <Button
              size="sm"
              variant="outline"
              onClick={() =>
                navigator.clipboard
                  .writeText(code)
                  .then(() => toast.success("注册码已复制"))
              }
            >
              <Copy />
              复制
            </Button>
          </div>
        ) : null}
      </div>
      <Surface className="overflow-hidden">
        {items.length ? (
          <div className="divide-y divide-slate-100">
            {items.map((item) => (
              <div
                key={String(item.worker_id)}
                className="grid gap-3 p-5 sm:grid-cols-[1fr_140px_180px]"
              >
                <div className="flex items-center gap-3">
                  <span className="grid h-10 w-10 place-items-center rounded-lg bg-slate-100">
                    <MonitorCog className="h-5 w-5" />
                  </span>
                  <div>
                    <p className="text-sm font-semibold text-slate-950">
                      {String(item.name)}
                    </p>
                    <p className="mt-1 text-xs text-slate-500">
                      版本 {String(item.version || "未知")} · 浏览器槽{" "}
                      {String(item.browser_slots || 1)}
                    </p>
                  </div>
                </div>
                <StatusBadge status={String(item.status)} />
                <p className="text-xs text-slate-500">
                  心跳 {formatDate(String(item.last_heartbeat_at || ""))}
                </p>
              </div>
            ))}
          </div>
        ) : (
          <EmptyState
            icon={<MonitorCog />}
            title="没有已注册设备"
            description="生成一次性注册码，并在抓取设备运行 Worker 注册命令。"
          />
        )}
      </Surface>
    </div>
  );
}
function Verifications() {
  const [items, setItems] = useState<Record<string, unknown>[]>([]),
    [loading, setLoading] = useState(true);
  const load = () => {
    setLoading(true);
    return remoteApi
      .adminVerifications()
      .then((response) => setItems(response.data.items))
      .catch(() => setItems([]))
      .finally(() => setLoading(false));
  };
  useEffect(() => {
    void load();
  }, []);
  const recheck = async (connectionId: string) => {
    try {
      await remoteApi.adminRecheckVerification(connectionId);
      toast.success("已通知执行设备重新检查登录状态");
      await load();
    } catch {
      toast.error("执行设备离线或暂时无法检查");
    }
  };
  return (
    <div>
      <PageHeader
        eyebrow="管理员"
        title="人工验证"
        description="验证码、短信或风险验证在这里进入等待状态。普通用户不会获得远程桌面入口。"
      />
      <Surface className="overflow-hidden">
        {items.length ? (
          <div className="divide-y divide-slate-100">
            {items.map((item) => (
              <div
                key={String(item.connection_id)}
                className="grid gap-3 p-5 sm:grid-cols-[1fr_160px_auto] sm:items-center"
              >
                <div>
                  <p className="text-sm font-semibold text-slate-950">
                    {String(item.user_display_name)} ·{" "}
                    {String(item.account_label)}
                  </p>
                  <p className="mt-1 text-xs text-slate-500">
                    执行设备 {String(item.worker_name)} · 更新于{" "}
                    {formatDate(String(item.updated_at || ""))}
                  </p>
                </div>
                <StatusBadge
                  status={String(item.status)}
                  label={
                    item.status === "risk_controlled"
                      ? "风控限制"
                      : "需要人工验证"
                  }
                />
                <Button
                  variant="outline"
                  disabled={item.worker_status !== "online"}
                  onClick={() => recheck(String(item.connection_id))}
                >
                  <RefreshCw />
                  已处理，重新检查
                </Button>
              </div>
            ))}
          </div>
        ) : (
          <EmptyState
            icon={<ShieldAlert />}
            title={loading ? "正在检查等待项" : "当前没有等待处理的验证"}
            description="当抖音要求人工确认时，相应账号会显示在这里。处理完成后再重新检查会话。"
          />
        )}
      </Surface>
    </div>
  );
}
function Queue() {
  const [items, setItems] = useState<Record<string, unknown>[]>([]),
    [loading, setLoading] = useState(true);
  const load = () => {
    setLoading(true);
    return remoteApi
      .adminQueue()
      .then((r) => setItems(r.data.items))
      .catch(() => setItems([]))
      .finally(() => setLoading(false));
  };
  useEffect(() => {
    void load();
  }, []);
  const pause = async (id: string) => {
    if (!confirm("紧急暂停该任务？Worker 会在安全检查点保存状态。")) return;
    try {
      await remoteApi.adminPauseRun(id);
      toast.success("已发送紧急暂停命令");
      await load();
    } catch {
      toast.error("该任务当前无法暂停");
    }
  };
  return (
    <div>
      <PageHeader
        eyebrow="管理员"
        title="全局任务队列"
        description="只展示脱敏的用户、账号、阶段与设备摘要，不提供业务内容访问。"
        actions={
          <Button variant="outline" onClick={load}>
            <RefreshCw />
            刷新
          </Button>
        }
      />
      <Surface className="overflow-hidden">
        {items.length ? (
          <div className="divide-y divide-slate-100">
            {items.map((item) => (
              <div
                key={String(item.run_id)}
                className="grid gap-3 p-4 sm:grid-cols-[1fr_140px_150px_auto] sm:items-center"
              >
                <div>
                  <p className="text-sm font-semibold">
                    {String(item.user_display_name)} ·{" "}
                    {String(item.account_label)}
                  </p>
                  <p className="mt-1 text-xs text-slate-500">
                    {String(item.crawler_type)} · {String(item.stage)} ·{" "}
                    {String(item.worker_name)}
                  </p>
                </div>
                <StatusBadge status={String(item.status)} />
                <p className="text-xs text-slate-500">
                  {formatDate(String(item.created_at))}
                </p>
                {[
                  "queued",
                  "running",
                  "pausing",
                  "waiting_for_login",
                  "waiting_for_space",
                ].includes(String(item.status)) ? (
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => pause(String(item.run_id))}
                  >
                    紧急暂停
                  </Button>
                ) : (
                  <span />
                )}
              </div>
            ))}
          </div>
        ) : (
          <EmptyState
            icon={<SquareStack />}
            title={loading ? "正在读取队列" : "队列为空"}
            description="当前没有远程采集任务。"
          />
        )}
      </Surface>
    </div>
  );
}
function Health() {
  const [data, setData] = useState<Record<string, unknown> | null>(null);
  const load = () =>
    Promise.all([systemApi.health(), systemApi.storage()]).then(([h, s]) =>
      setData({ health: h.data, storage: s.data }),
    );
  useEffect(() => {
    void load();
  }, []);
  const checks = ((data?.health as Record<string, unknown> | undefined)
      ?.checks || {}) as Record<string, Record<string, unknown>>,
    storage = (data?.storage || {}) as Record<string, number>;
  return (
    <div>
      <PageHeader
        eyebrow="管理员"
        title="系统健康"
        description="检查 Chrome、ASR、媒体校验、全文检索、任务库与磁盘空间。"
        actions={
          <Button variant="outline" onClick={load}>
            <RefreshCw />
            重新检查
          </Button>
        }
      />
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {Object.entries(checks).map(([name, check]) => (
          <Surface key={name} className="p-5">
            <div className="flex items-start justify-between">
              <span
                className={`grid h-10 w-10 place-items-center rounded-lg ${check.ok ? "bg-emerald-50 text-emerald-700" : "bg-amber-50 text-amber-800"}`}
              >
                {check.ok ? (
                  <CheckCircle2 className="h-5 w-5" />
                ) : (
                  <AlertTriangle className="h-5 w-5" />
                )}
              </span>
              <StatusBadge
                status={check.ok ? "connected" : "offline"}
                label={check.ok ? "正常" : "需关注"}
              />
            </div>
            <p className="mt-4 text-sm font-semibold text-slate-950">
              {(
                {
                  cdp: "Chrome / CDP",
                  faster_whisper: "本地 ASR",
                  ffprobe: "ffprobe 媒体校验",
                  sqlite_fts5: "SQLite 全文检索",
                  media_writable: "媒体目录",
                  task_database: "任务数据库",
                } as Record<string, string>
              )[name] || name}
            </p>
            <p className="mt-1 text-xs leading-5 text-slate-500">
              {String(
                check.detail ||
                  check.path ||
                  check.device ||
                  check.fallback ||
                  "状态已检查",
              )}
            </p>
          </Surface>
        ))}
      </div>
      <Surface className="mt-4 p-5">
        <div className="flex items-center gap-2">
          <HardDrive className="h-5 w-5 text-teal-700" />
          <h2 className="text-sm font-semibold">存储空间</h2>
        </div>
        <div className="mt-4 grid grid-cols-3 gap-4">
          <Metric label="媒体使用" value={formatBytes(storage.media_bytes)} />
          <Metric label="磁盘剩余" value={formatBytes(storage.free_bytes)} />
          <Metric
            label="媒体上限"
            value={formatBytes(storage.library_limit_bytes)}
          />
        </div>
      </Surface>
    </div>
  );
}
function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-xs text-slate-500">{label}</p>
      <p className="mt-1 text-lg font-semibold">{value}</p>
    </div>
  );
}
