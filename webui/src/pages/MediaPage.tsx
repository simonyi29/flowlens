import { FormEvent, useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  ChevronLeft,
  ChevronRight,
  Clapperboard,
  FileImage,
  FileMusic,
  FileVideo,
  HardDrive,
  RefreshCw,
  Search,
  ShieldCheck,
  Trash2,
  X,
} from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  EmptyState,
  PageHeader,
  StatusBadge,
  Surface,
} from "@/components/product/Primitives";
import { mediaApi, remoteApi, systemApi } from "@/lib/api";
import { useCapabilities } from "@/hooks/useProduct";
import { formatBytes, formatDuration } from "@/lib/presentation";
import type { MediaListResponse, MediaSummary } from "@/types/product";

const PAGE_SIZE = 8;
const kinds = ["", "video", "image", "cover", "music"] as const;
const statuses = [
  "active",
  "completed",
  "downloading",
  "partial",
  "waiting_for_space",
  "failed",
  "deleted",
] as const;
const sorts = ["newest", "oldest", "largest"] as const;

const emptyResponse: MediaListResponse = {
  items: [],
  total: 0,
  filtered_bytes: 0,
  active_total: 0,
  status_counts: {},
  kind_counts: {},
  limit: PAGE_SIZE,
  offset: 0,
};

export default function MediaPage() {
  const { t } = useTranslation("product");
  const capabilities = useCapabilities();
  const remote = Boolean(capabilities.data?.features.remote_worker);
  const [catalog, setCatalog] = useState<MediaListResponse>(emptyResponse);
  const [storage, setStorage] = useState<Record<string, number>>({});
  const [loading, setLoading] = useState(false);
  const [page, setPage] = useState(1);
  const [searchDraft, setSearchDraft] = useState("");
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState("");
  const [status, setStatus] = useState("active");
  const [sort, setSort] = useState("newest");
  const [deleteTarget, setDeleteTarget] = useState<MediaSummary | null>(null);
  const [connections, setConnections] = useState<Record<string, unknown>[]>([]);
  const [connectionId, setConnectionId] = useState("");
  const [deleting, setDeleting] = useState(false);

  const load = useCallback(
    async (quiet = false) => {
      if (!capabilities.data) return;
      if (!quiet) setLoading(true);
      try {
        if (remote) {
          const response = await remoteApi.results("media", {
            limit: 500,
            offset: 0,
            connection_id: connectionId || undefined,
          });
          if (Number(response.data.total || 0) > response.data.items.length) {
            toast.warning(
              t("media.remoteLimitWarning", { total: response.data.total }),
            );
          }
          const all = response.data.items.map(
            (row: Record<string, unknown>) => ({
              ...((row.payload || {}) as Record<string, unknown>),
              asset_id: row.entity_id,
              account_label: row.account_label,
            }),
          ) as MediaSummary[];
          const normalizedQuery = query.toLocaleLowerCase();
          const filtered = all
            .filter((item) => {
              if (
                status === "active"
                  ? item.status === "deleted"
                  : item.status !== status
              )
                return false;
              if (kind && item.kind !== kind) return false;
              if (!normalizedQuery) return true;
              return [
                item.aweme_id,
                item.creator_hash,
                item.quality,
                item.mime_type,
              ].some((value) =>
                String(value || "")
                  .toLocaleLowerCase()
                  .includes(normalizedQuery),
              );
            })
            .sort((left, right) =>
              sort === "largest"
                ? Number(right.size_bytes || 0) - Number(left.size_bytes || 0)
                : String(
                    sort === "oldest"
                      ? left.updated_at || ""
                      : right.updated_at || "",
                  ).localeCompare(
                    String(
                      sort === "oldest"
                        ? right.updated_at || ""
                        : left.updated_at || "",
                    ),
                  ),
            );
          const offset = (page - 1) * PAGE_SIZE;
          const active = all.filter((item) => item.status !== "deleted");
          setCatalog({
            items: filtered.slice(offset, offset + PAGE_SIZE),
            total: filtered.length,
            filtered_bytes: filtered.reduce(
              (sum, item) => sum + Number(item.size_bytes || 0),
              0,
            ),
            active_total: active.length,
            status_counts: countBy(all, (item) => item.status),
            kind_counts: countBy(active, (item) => item.kind),
            limit: PAGE_SIZE,
            offset,
          });
        } else {
          const response = await mediaApi.list({
            limit: PAGE_SIZE,
            offset: (page - 1) * PAGE_SIZE,
            q: query || undefined,
            kind: kind || undefined,
            status,
            sort,
          });
          setCatalog(response.data);
        }
        const storageResponse = await systemApi.storage();
        setStorage(storageResponse.data);
      } catch {
        if (!quiet) toast.error(t("media.loadFailed"));
      } finally {
        if (!quiet) setLoading(false);
      }
    },
    [
      capabilities.data,
      connectionId,
      kind,
      page,
      query,
      remote,
      sort,
      status,
      t,
    ],
  );

  useEffect(() => {
    void load();
  }, [load]);
  useEffect(() => {
    if (remote)
      remoteApi
        .connections()
        .then((response) =>
          setConnections(
            response.data.items.filter(
              (item: Record<string, unknown>) => item.status !== "disconnected",
            ),
          ),
        )
        .catch(() => setConnections([]));
  }, [remote]);

  const pages = Math.max(1, Math.ceil(catalog.total / PAGE_SIZE));
  useEffect(() => {
    if (page > pages) setPage(pages);
  }, [page, pages]);

  const hasFilters = Boolean(
    query || kind || status !== "active" || sort !== "newest",
  );
  const applySearch = (event: FormEvent) => {
    event.preventDefault();
    setPage(1);
    setQuery(searchDraft.trim());
  };
  const clearFilters = () => {
    setSearchDraft("");
    setQuery("");
    setKind("");
    setStatus("active");
    setSort("newest");
    setPage(1);
  };
  const remove = async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      if (remote) await remoteApi.deleteMedia(deleteTarget.asset_id);
      else await mediaApi.remove(deleteTarget.asset_id);
      toast.success(
        remote ? "删除命令已发送，数据库记录会保留" : t("media.deleteSuccess"),
      );
      setDeleteTarget(null);
      await load(true);
    } catch (error) {
      const detail = (error as { response?: { data?: { detail?: string } } })
        .response?.data?.detail;
      toast.error(
        detail
          ? `${t("media.deleteFailed")}：${detail}`
          : t("media.deleteFailed"),
      );
    } finally {
      setDeleting(false);
    }
  };

  return (
    <div>
      <PageHeader
        eyebrow={t("media.eyebrow")}
        title={t("media.title")}
        description={t("media.description")}
        actions={
          <Button
            variant="outline"
            onClick={() => void load()}
            disabled={loading}
          >
            <RefreshCw className={loading ? "animate-spin" : ""} />
            {t("media.refresh")}
          </Button>
        }
      />
      <div className="mb-4 grid gap-3 sm:grid-cols-3">
        <Surface className="p-4">
          <p className="flex items-center gap-2 text-xs text-slate-500">
            <HardDrive className="h-4 w-4" />
            {t("media.usage")}
          </p>
          <p className="mt-2 text-xl font-semibold">
            {formatBytes(storage.media_bytes)}
          </p>
        </Surface>
        <Surface className="p-4">
          <p className="text-xs text-slate-500">{t("media.diskFree")}</p>
          <p className="mt-2 text-xl font-semibold">
            {formatBytes(storage.free_bytes)}
          </p>
        </Surface>
        <Surface className="p-4">
          <p className="text-xs text-slate-500">{t("media.assetCount")}</p>
          <p className="mt-2 text-xl font-semibold">{catalog.active_total}</p>
        </Surface>
      </div>
      <Surface className="mb-4 p-4">
        <form
          onSubmit={applySearch}
          className="grid gap-3 lg:grid-cols-[minmax(260px,1.4fr)_minmax(150px,.65fr)_minmax(170px,.72fr)_minmax(150px,.65fr)_auto] lg:items-end"
        >
          {remote && connections.length > 1 ? (
            <FilterSelect
              label="抖音账号"
              value={connectionId}
              onChange={(value) => {
                setConnectionId(value);
                setPage(1);
              }}
            >
              <option value="">全部账号</option>
              {connections.map((item) => (
                <option
                  key={String(item.connection_id)}
                  value={String(item.connection_id)}
                >
                  {String(
                    item.display_name ||
                      item.remark ||
                      item.masked_nickname ||
                      "抖音账号",
                  )}
                </option>
              ))}
            </FilterSelect>
          ) : null}
          <label className="block text-xs font-medium text-slate-600">
            <span className="mb-1.5 block">{t("media.searchLabel")}</span>
            <div className="flex gap-2">
              <Input
                value={searchDraft}
                onChange={(event) => setSearchDraft(event.target.value)}
                placeholder={t("media.searchPlaceholder")}
              />
              <Button type="submit" variant="secondary">
                <Search />
                {t("media.search")}
              </Button>
            </div>
          </label>
          <FilterSelect
            label={t("media.kindLabel")}
            value={kind}
            onChange={(value) => {
              setKind(value);
              setPage(1);
            }}
          >
            {kinds.map((value) => (
              <option key={value || "all"} value={value}>
                {value
                  ? `${t(`media.kinds.${value}`)} (${catalog.kind_counts[value] || 0})`
                  : t("media.allKinds")}
              </option>
            ))}
          </FilterSelect>
          <FilterSelect
            label={t("media.statusLabel")}
            value={status}
            onChange={(value) => {
              setStatus(value);
              setPage(1);
            }}
          >
            {statuses.map((value) => (
              <option key={value} value={value}>
                {t(`media.statuses.${value}`)}
                {value !== "active"
                  ? ` (${catalog.status_counts[value] || 0})`
                  : ""}
              </option>
            ))}
          </FilterSelect>
          <FilterSelect
            label={t("media.sortLabel")}
            value={sort}
            onChange={(value) => {
              setSort(value);
              setPage(1);
            }}
          >
            {sorts.map((value) => (
              <option key={value} value={value}>
                {t(`media.sorts.${value}`)}
              </option>
            ))}
          </FilterSelect>
          <Button
            type="button"
            variant="ghost"
            onClick={clearFilters}
            disabled={!hasFilters}
          >
            <X />
            {t("media.clearFilters")}
          </Button>
        </form>
      </Surface>
      <div className="mb-3 flex items-center justify-between gap-3">
        <p className="text-xs text-slate-500">
          {t("media.resultSummary", { total: catalog.total })}
        </p>
        {catalog.filtered_bytes > 0 ? (
          <p className="text-xs tabular-nums text-slate-500">
            {formatBytes(catalog.filtered_bytes)}
          </p>
        ) : null}
      </div>
      {catalog.items.length ? (
        <>
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {catalog.items.map((item) => (
              <MediaCard
                key={item.asset_id}
                item={item}
                remote={remote}
                onDelete={() => setDeleteTarget(item)}
              />
            ))}
          </div>
          <div className="mt-5 flex flex-col gap-3 rounded-xl border border-slate-200 bg-white px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
            <p className="text-center text-xs text-slate-500 sm:text-left">
              {t("media.pageSummary", { page, pages, total: catalog.total })}
            </p>
            <div className="flex justify-center gap-2">
              <Button
                size="sm"
                variant="outline"
                disabled={page <= 1 || loading}
                onClick={() => setPage((value) => value - 1)}
              >
                <ChevronLeft />
                {t("media.previous")}
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={page >= pages || loading}
                onClick={() => setPage((value) => value + 1)}
              >
                {t("media.next")}
                <ChevronRight />
              </Button>
            </div>
          </div>
        </>
      ) : (
        <Surface>
          <EmptyState
            icon={<Clapperboard />}
            title={
              hasFilters ? t("media.filteredEmptyTitle") : t("media.emptyTitle")
            }
            description={
              hasFilters
                ? t("media.filteredEmptyDescription")
                : t("media.emptyDescription")
            }
            action={
              hasFilters ? (
                <Button variant="outline" onClick={clearFilters}>
                  {t("media.clearFilters")}
                </Button>
              ) : undefined
            }
          />
        </Surface>
      )}
      <DeleteMediaDialog
        item={deleteTarget}
        open={Boolean(deleteTarget)}
        deleting={deleting}
        onOpenChange={(open) => {
          if (!open && !deleting) setDeleteTarget(null);
        }}
        onConfirm={remove}
      />
    </div>
  );
}

function FilterSelect({
  label,
  value,
  onChange,
  children,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  children: React.ReactNode;
}) {
  return (
    <label className="block text-xs font-medium text-slate-600">
      <span className="mb-1.5 block">{label}</span>
      <select
        className="h-11 w-full rounded-lg border border-slate-300 bg-white px-3 text-sm text-slate-800 focus-visible:border-teal-700 focus-visible:ring-2 focus-visible:ring-teal-700/15"
        value={value}
        onChange={(event) => onChange(event.target.value)}
      >
        {children}
      </select>
    </label>
  );
}

function MediaCard({
  item,
  remote,
  onDelete,
}: {
  item: MediaSummary;
  remote: boolean;
  onDelete: () => void;
}) {
  const { t } = useTranslation("product");
  const Icon =
    item.kind === "video"
      ? FileVideo
      : item.kind === "music"
        ? FileMusic
        : FileImage;
  const available =
    item.status === "completed" && (remote || Number(item.size_bytes || 0) > 0);
  const url = remote
    ? remoteApi.mediaUrl(item.asset_id)
    : mediaApi.streamUrl(item.asset_id);
  return (
    <Surface className="overflow-hidden">
      <div className="relative grid aspect-video place-items-center bg-slate-950 text-slate-500">
        {available && item.kind === "video" ? (
          <video
            aria-label={t("media.work", { id: item.aweme_id })}
            className="h-full w-full object-contain"
            controls
            preload="metadata"
            src={url}
          />
        ) : available && ["image", "cover"].includes(item.kind) ? (
          <img
            className="h-full w-full object-contain"
            loading="lazy"
            src={url}
            alt={t("media.work", { id: item.aweme_id })}
          />
        ) : available && item.kind === "music" ? (
          <audio
            className="w-[calc(100%-2rem)]"
            controls
            preload="none"
            src={url}
          />
        ) : (
          <Icon className="h-10 w-10" />
        )}
        <span className="absolute left-3 top-3">
          <StatusBadge
            status={item.status}
            label={t(`media.statuses.${item.status}`)}
          />
        </span>
      </div>
      <div className="p-4">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="truncate text-sm font-semibold text-slate-950">
              {t("media.work", { id: item.aweme_id })}
            </p>
            <p className="mt-1 text-xs text-slate-500">
              {t(`media.kinds.${item.kind}`, { defaultValue: item.kind })} ·{" "}
              {item.quality || t("media.defaultQuality")} ·{" "}
              {formatBytes(item.size_bytes)}
            </p>
          </div>
          {item.sha256 ? (
            <ShieldCheck
              className="h-5 w-5 shrink-0 text-emerald-600"
              aria-label={t("media.verified")}
            />
          ) : null}
        </div>
        {"account_label" in item && item.account_label ? (
          <p className="mt-2 truncate text-xs text-slate-500">
            账号：{String(item.account_label)}
          </p>
        ) : null}
        <div className="mt-4 flex items-center justify-between gap-3 text-xs text-slate-500">
          <span className="min-w-0 truncate">
            {item.duration_ms
              ? formatDuration(item.duration_ms / 1000)
              : item.mime_type || t("media.mediaFile")}
          </span>
          {item.status !== "deleted" ? (
            <Button size="sm" variant="ghost" onClick={onDelete}>
              <Trash2 />
              {t("media.delete")}
            </Button>
          ) : null}
        </div>
      </div>
    </Surface>
  );
}

function DeleteMediaDialog({
  item,
  open,
  deleting,
  onOpenChange,
  onConfirm,
}: {
  item: MediaSummary | null;
  open: boolean;
  deleting: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: () => void;
}) {
  const { t } = useTranslation("product");
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="border-slate-200 bg-white text-slate-900 sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="font-sans text-lg text-slate-950">
            {t("media.deleteTitle")}
          </DialogTitle>
          <DialogDescription className="leading-6 text-slate-600">
            {t("media.deleteDescription")}
          </DialogDescription>
        </DialogHeader>
        {item ? (
          <div className="rounded-lg bg-slate-50 p-3 text-sm text-slate-700">
            {t("media.deleteTarget", {
              kind: t(`media.kinds.${item.kind}`, { defaultValue: item.kind }),
              size: formatBytes(item.size_bytes),
              id: item.aweme_id,
            })}
          </div>
        ) : null}
        <DialogFooter className="gap-2 sm:space-x-0">
          <Button
            variant="outline"
            disabled={deleting}
            onClick={() => onOpenChange(false)}
          >
            {t("media.deleteCancel")}
          </Button>
          <Button variant="destructive" disabled={deleting} onClick={onConfirm}>
            {deleting ? <RefreshCw className="animate-spin" /> : <Trash2 />}
            {deleting ? t("media.deleting") : t("media.deleteConfirm")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function countBy(items: MediaSummary[], key: (item: MediaSummary) => string) {
  return items.reduce<Record<string, number>>((counts, item) => {
    const value = key(item);
    counts[value] = (counts[value] || 0) + 1;
    return counts;
  }, {});
}
