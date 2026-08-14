import { useEffect, useState, type FormEvent } from "react";
import {
  Copy,
  KeyRound,
  LogOut,
  MoreHorizontal,
  Pencil,
  Plus,
  RefreshCw,
  Search,
  UserRoundCheck,
  UserRoundX,
} from "lucide-react";
import { toast } from "sonner";
import { adminUserApi, type AdminUser } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  EmptyState,
  PageHeader,
  StatusBadge,
  Surface,
} from "@/components/product/Primitives";
import { formatBytes, formatDate } from "@/lib/presentation";

interface TemporaryCredential {
  username: string;
  password: string;
  expires: string;
}

export default function AdminUsersPage() {
  const [items, setItems] = useState<AdminUser[]>([]),
    [search, setSearch] = useState(""),
    [loading, setLoading] = useState(true),
    [creating, setCreating] = useState(false),
    [credential, setCredential] = useState<TemporaryCredential | null>(null),
    [editing, setEditing] = useState<AdminUser | null>(null);
  const load = async () => {
    setLoading(true);
    try {
      setItems(
        (await adminUserApi.list({ search: search || undefined })).data.items,
      );
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => {
    void load();
  }, []);
  return (
    <div>
      <PageHeader
        eyebrow="账号管理"
        title="用户账号"
        description="普通用户只能由管理员创建。管理员不会获得用户的业务数据或抖音账号访问权。"
        actions={
          <>
            <Button variant="outline" onClick={load}>
              <RefreshCw />
              刷新
            </Button>
            <Button onClick={() => setCreating(true)}>
              <Plus />
              创建用户
            </Button>
          </>
        }
      />
      <Surface className="mb-4 p-4">
        <form
          className="flex max-w-lg gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            void load();
          }}
        >
          <div className="relative flex-1">
            <Search className="absolute left-3 top-3 h-4 w-4 text-slate-400" />
            <Input
              className="pl-9"
              placeholder="搜索用户名或显示名称"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
          <Button type="submit" variant="outline">
            搜索
          </Button>
        </form>
      </Surface>
      <Surface className="overflow-hidden">
        {items.length ? (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[980px] text-left text-sm">
              <thead className="border-b border-slate-200 bg-slate-50 text-xs text-slate-500">
                <tr>
                  <th className="px-5 py-3 font-medium">用户</th>
                  <th className="px-5 py-3 font-medium">状态</th>
                  <th className="px-5 py-3 font-medium">抖音账号</th>
                  <th className="px-5 py-3 font-medium">任务</th>
                  <th className="px-5 py-3 font-medium">媒体配额</th>
                  <th className="px-5 py-3 font-medium">最后登录</th>
                  <th className="px-5 py-3 font-medium text-right">操作</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {items.map((item) => (
                  <UserRow
                    key={item.user_id}
                    item={item}
                    reload={load}
                    reveal={setCredential}
                    edit={setEditing}
                  />
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState
            icon={<UserRoundCheck />}
            title={loading ? "正在读取用户" : "还没有普通用户"}
            description="点击“创建用户”生成用户名和一次性临时密码。"
          />
        )}
      </Surface>
      {creating ? (
        <CreateDialog
          close={() => setCreating(false)}
          done={(value) => {
            setCreating(false);
            setCredential(value);
            void load();
          }}
        />
      ) : null}
      {credential ? (
        <CredentialDialog
          value={credential}
          close={() => setCredential(null)}
        />
      ) : null}
      {editing ? (
        <EditDialog
          item={editing}
          close={() => setEditing(null)}
          done={() => {
            setEditing(null);
            void load();
          }}
        />
      ) : null}
    </div>
  );
}

function UserRow({
  item,
  reload,
  reveal,
  edit,
}: {
  item: AdminUser;
  reload: () => Promise<void>;
  reveal: (value: TemporaryCredential) => void;
  edit: (item: AdminUser) => void;
}) {
  const [open, setOpen] = useState(false);
  const reset = async () => {
    if (
      !confirm(`为 ${item.username} 重新生成临时密码？这会撤销该用户全部会话。`)
    )
      return;
    const r = await adminUserApi.resetPassword(item.user_id);
    reveal({
      username: item.username,
      password: r.data.temporary_password,
      expires: r.data.temporary_password_expires_at,
    });
    await reload();
  };
  const toggle = async () => {
    if (item.role === "admin") return;
    if (item.status === "suspended") {
      await adminUserApi.restore(item.user_id);
      toast.success("用户已恢复");
    } else {
      if (
        !confirm(
          `暂停 ${item.username}？其网站会话会立即失效，未完成任务将暂停。`,
        )
      )
        return;
      await adminUserApi.suspend(item.user_id);
      toast.success("用户已暂停");
    }
    setOpen(false);
    await reload();
  };
  const revoke = async () => {
    if (!confirm(`撤销 ${item.username} 的全部网站会话？`)) return;
    const response = await adminUserApi.revokeSessions(item.user_id);
    toast.success(`已撤销 ${response.data.revoked_sessions || 0} 个会话`);
    setOpen(false);
  };
  return (
    <tr>
      <td className="px-5 py-4">
        <p className="font-semibold text-slate-950">{item.display_name}</p>
        <p className="mt-1 text-xs text-slate-500">
          {item.username}
          {item.role === "admin" ? " · 管理员" : ""}
        </p>
      </td>
      <td className="px-5 py-4">
        <StatusBadge
          status={item.status}
          label={
            (
              {
                active: "已启用",
                pending_activation: "待激活",
                suspended: "已暂停",
              } as Record<string, string>
            )[item.status] || item.status
          }
        />
      </td>
      <td className="px-5 py-4 text-slate-600">
        {item.douyin_connection_count || 0} / {item.max_douyin_connections}
      </td>
      <td className="px-5 py-4 text-slate-600">
        {item.active_task_count || 0} / {item.max_queued_tasks}
      </td>
      <td className="px-5 py-4 text-slate-600">
        {formatBytes(item.media_usage_bytes || 0)} /{" "}
        {formatBytes(item.media_quota_bytes)}
      </td>
      <td className="px-5 py-4 text-slate-600">
        {formatDate(item.last_login_at || "")}
      </td>
      <td className="relative px-5 py-4 text-right">
        <Button
          size="sm"
          variant="ghost"
          aria-label={`管理 ${item.username}`}
          onClick={() => setOpen(!open)}
        >
          <MoreHorizontal />
        </Button>
        {open ? (
          <div className="absolute right-5 top-12 z-20 w-52 rounded-xl border border-slate-200 bg-white p-1 text-left shadow-lg">
            <button
              className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-sm hover:bg-slate-50"
              onClick={() => {
                setOpen(false);
                edit(item);
              }}
            >
              <Pencil className="h-4 w-4" />
              修改资料与配额
            </button>
            <button
              className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-sm hover:bg-slate-50"
              onClick={reset}
            >
              <KeyRound className="h-4 w-4" />
              重置临时密码
            </button>
            <button
              className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-sm hover:bg-slate-50"
              onClick={revoke}
            >
              <LogOut className="h-4 w-4" />
              撤销全部会话
            </button>
            <button
              disabled={item.role === "admin"}
              className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-sm hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40"
              onClick={toggle}
            >
              {item.status === "suspended" ? (
                <UserRoundCheck className="h-4 w-4" />
              ) : (
                <UserRoundX className="h-4 w-4" />
              )}
              {item.status === "suspended" ? "恢复用户" : "暂停用户"}
            </button>
          </div>
        ) : null}
      </td>
    </tr>
  );
}

function CreateDialog({
  close,
  done,
}: {
  close: () => void;
  done: (value: TemporaryCredential) => void;
}) {
  const [form, setForm] = useState({
      username: "",
      display_name: "",
      max_douyin_connections: 3,
      max_queued_tasks: 10,
      media_quota_bytes: 20 * 1024 ** 3,
    }),
    [busy, setBusy] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      const r = await adminUserApi.create(form);
      done({
        username: r.data.user.username,
        password: r.data.temporary_password,
        expires: r.data.temporary_password_expires_at,
      });
    } catch (error: any) {
      toast.error(error?.response?.data?.detail?.user_message || "创建失败");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-slate-950/40 p-4">
      <form
        onSubmit={submit}
        className="w-full max-w-lg rounded-2xl bg-white p-6 shadow-xl"
      >
        <h2 className="text-lg font-semibold">创建普通用户</h2>
        <p className="mt-1 text-sm text-slate-500">
          角色固定为普通用户，管理员不能在网页创建另一个管理员。
        </p>
        <div className="mt-5 grid gap-4 sm:grid-cols-2">
          <Field label="用户名">
            <Input
              required
              pattern="[a-z0-9._-]{3,32}"
              value={form.username}
              onChange={(e) =>
                setForm({ ...form, username: e.target.value.toLowerCase() })
              }
            />
          </Field>
          <Field label="显示名称">
            <Input
              required
              value={form.display_name}
              onChange={(e) =>
                setForm({ ...form, display_name: e.target.value })
              }
            />
          </Field>
          <Field label="抖音账号上限">
            <Input
              type="number"
              min={1}
              max={50}
              value={form.max_douyin_connections}
              onChange={(e) =>
                setForm({
                  ...form,
                  max_douyin_connections: Number(e.target.value),
                })
              }
            />
          </Field>
          <Field label="排队任务上限">
            <Input
              type="number"
              min={1}
              max={1000}
              value={form.max_queued_tasks}
              onChange={(e) =>
                setForm({ ...form, max_queued_tasks: Number(e.target.value) })
              }
            />
          </Field>
        </div>
        <div className="mt-6 flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={close}>
            取消
          </Button>
          <Button disabled={busy}>创建并生成临时密码</Button>
        </div>
      </form>
    </div>
  );
}

function EditDialog({
  item,
  close,
  done,
}: {
  item: AdminUser;
  close: () => void;
  done: () => void;
}) {
  const [form, setForm] = useState({
      username: item.username,
      display_name: item.display_name,
      max_douyin_connections: item.max_douyin_connections,
      max_queued_tasks: item.max_queued_tasks,
      media_quota_gb: Math.max(
        1,
        Math.round(item.media_quota_bytes / 1024 ** 3),
      ),
    }),
    [busy, setBusy] = useState(false);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    try {
      await adminUserApi.update(item.user_id, {
        username: form.username,
        display_name: form.display_name,
        max_douyin_connections: form.max_douyin_connections,
        max_queued_tasks: form.max_queued_tasks,
        media_quota_bytes: form.media_quota_gb * 1024 ** 3,
      });
      toast.success("用户资料与配额已更新");
      done();
    } catch (error: any) {
      toast.error(error?.response?.data?.detail?.user_message || "更新失败");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-slate-950/40 p-4">
      <form
        onSubmit={submit}
        className="w-full max-w-lg rounded-2xl bg-white p-6 shadow-xl"
      >
        <h2 className="text-lg font-semibold">修改用户</h2>
        <p className="mt-1 text-sm text-slate-500">
          只调整账号资料和资源配额，不会展示用户业务数据。
        </p>
        <div className="mt-5 grid gap-4 sm:grid-cols-2">
          <Field label="用户名">
            <Input
              required
              pattern="[a-z0-9._-]{3,32}"
              value={form.username}
              onChange={(event) =>
                setForm({ ...form, username: event.target.value.toLowerCase() })
              }
            />
          </Field>
          <Field label="显示名称">
            <Input
              required
              value={form.display_name}
              onChange={(event) =>
                setForm({ ...form, display_name: event.target.value })
              }
            />
          </Field>
          <Field label="抖音账号上限">
            <Input
              type="number"
              min={1}
              max={50}
              value={form.max_douyin_connections}
              onChange={(event) =>
                setForm({
                  ...form,
                  max_douyin_connections: Number(event.target.value),
                })
              }
            />
          </Field>
          <Field label="排队任务上限">
            <Input
              type="number"
              min={1}
              max={1000}
              value={form.max_queued_tasks}
              onChange={(event) =>
                setForm({
                  ...form,
                  max_queued_tasks: Number(event.target.value),
                })
              }
            />
          </Field>
          <Field label="媒体配额（GB）">
            <Input
              type="number"
              min={1}
              max={10240}
              value={form.media_quota_gb}
              onChange={(event) =>
                setForm({ ...form, media_quota_gb: Number(event.target.value) })
              }
            />
          </Field>
        </div>
        <div className="mt-6 flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={close}>
            取消
          </Button>
          <Button disabled={busy}>{busy ? "正在保存…" : "保存修改"}</Button>
        </div>
      </form>
    </div>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <label className="text-sm font-medium text-slate-700">
      {label}
      <span className="mt-2 block">{children}</span>
    </label>
  );
}
function CredentialDialog({
  value,
  close,
}: {
  value: TemporaryCredential;
  close: () => void;
}) {
  const copy = () =>
    navigator.clipboard
      .writeText(
        `FlowLens 登录信息\n用户名：${value.username}\n临时密码：${value.password}\n有效期：${value.expires}`,
      )
      .then(() => toast.success("登录信息已复制"));
  return (
    <div className="fixed inset-0 z-[60] grid place-items-center bg-slate-950/50 p-4">
      <div className="w-full max-w-lg rounded-2xl bg-white p-6 shadow-xl">
        <p className="text-sm font-semibold text-amber-700">仅显示一次</p>
        <h2 className="mt-1 text-lg font-semibold">请立即保存临时密码</h2>
        <p className="mt-2 text-sm text-slate-600">
          关闭此窗口后无法再次查看，只能重新生成。用户须在 24
          小时内登录并修改密码。
        </p>
        <dl className="mt-5 space-y-3 rounded-xl bg-slate-50 p-4">
          <div>
            <dt className="text-xs text-slate-500">用户名</dt>
            <dd className="mt-1 font-mono text-sm">{value.username}</dd>
          </div>
          <div>
            <dt className="text-xs text-slate-500">临时密码</dt>
            <dd className="mt-1 break-all font-mono text-sm font-semibold">
              {value.password}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-slate-500">有效期至</dt>
            <dd className="mt-1 text-sm">{formatDate(value.expires)}</dd>
          </div>
        </dl>
        <div className="mt-6 flex justify-end gap-2">
          <Button variant="outline" onClick={copy}>
            <Copy />
            复制登录信息
          </Button>
          <Button onClick={close}>我已保存</Button>
        </div>
      </div>
    </div>
  );
}
