import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  Captions,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Download,
  FileText,
  Filter,
  Library,
  MessageSquareText,
  Search,
  Tags,
  UserRound,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  EmptyState,
  PageHeader,
  StatusBadge,
  Surface,
} from "@/components/product/Primitives";
import { libraryApi, remoteApi } from "@/lib/api";
import { useCapabilities } from "@/hooks/useProduct";
import { formatDate } from "@/lib/presentation";

type Kind = "awemes" | "creators" | "topics" | "comments" | "transcripts";
type Row = Record<string, unknown>;

const kinds: Array<{
  id: Kind;
  label: string;
  icon: typeof FileText;
  remote: string;
}> = [
  { id: "awemes", label: "作品", icon: FileText, remote: "aweme" },
  { id: "creators", label: "账号", icon: UserRound, remote: "creator" },
  { id: "topics", label: "话题", icon: Tags, remote: "topic" },
  { id: "comments", label: "评论", icon: MessageSquareText, remote: "comment" },
  { id: "transcripts", label: "字幕", icon: Captions, remote: "transcript" },
];
const countLabels: Record<string, string> = {
  awemes: "作品",
  creators: "账号",
  topics: "话题",
  comments: "一级评论",
  replies: "二级回复",
  transcripts: "字幕",
  media: "本地媒体",
};

export default function LibraryPage() {
  const capabilities = useCapabilities();
  const remote = capabilities.data?.features.remote_worker;
  const [params, setParams] = useSearchParams();
  const kind = (params.get("kind") as Kind) || "awemes";
  const page = Math.max(1, Number(params.get("page") || 1));
  const [items, setItems] = useState<Row[]>([]);
  const [q, setQ] = useState(params.get("q") || "");
  const [stats, setStats] = useState<Record<string, number>>({});
  const [total, setTotal] = useState(0);
  const [showFilters, setShowFilters] = useState(false);
  const [creator, setCreator] = useState(params.get("creator_hash") || "");
  const [source, setSource] = useState(params.get("source") || "");
  const [minLikes, setMinLikes] = useState(params.get("min_likes") || "");
  const [maxLikes, setMaxLikes] = useState(params.get("max_likes") || "");
  const [minComments, setMinComments] = useState(
    params.get("min_comments") || "",
  );
  const [minPlays, setMinPlays] = useState(params.get("min_plays") || "");
  const [publishedFrom, setPublishedFrom] = useState(
    params.get("published_from_date") || "",
  );
  const [publishedTo, setPublishedTo] = useState(
    params.get("published_to_date") || "",
  );
  const [transcriptStatus, setTranscriptStatus] = useState(
    params.get("transcript_status") || "",
  );
  const [commentStatus, setCommentStatus] = useState(
    params.get("comment_status") || "",
  );
  const [downloadStatus, setDownloadStatus] = useState(
    params.get("download_status") || "",
  );
  const [connections, setConnections] = useState<Row[]>([]);
  const [connectionId, setConnectionId] = useState(
    params.get("connection_id") || "",
  );
  const pageSize = 20;

  const queryFilters = useMemo(
    () => ({
      creator_hash: creator || undefined,
      source_topic: source || undefined,
      min_likes: minLikes || undefined,
      max_likes: maxLikes || undefined,
      min_comments: minComments || undefined,
      min_plays: minPlays || undefined,
      published_from: publishedFrom
        ? Math.floor(new Date(`${publishedFrom}T00:00:00`).getTime() / 1000)
        : undefined,
      published_to: publishedTo
        ? Math.floor(new Date(`${publishedTo}T23:59:59`).getTime() / 1000)
        : undefined,
      transcript_status: transcriptStatus || undefined,
      comment_status: commentStatus || undefined,
      download_status: downloadStatus || undefined,
    }),
    [
      creator,
      source,
      minLikes,
      maxLikes,
      minComments,
      minPlays,
      publishedFrom,
      publishedTo,
      transcriptStatus,
      commentStatus,
      downloadStatus,
    ],
  );

  const paramsKey = params.toString();
  const commitUrl = (
    nextPage = 1,
    nextKind = kind,
    overrides?: { connection_id?: string },
  ) => {
    const next = new URLSearchParams({
      kind: nextKind,
      page: String(nextPage),
    });
    const values = {
      q,
      connection_id: overrides?.connection_id ?? connectionId,
      creator_hash: creator,
      source,
      min_likes: minLikes,
      max_likes: maxLikes,
      min_comments: minComments,
      min_plays: minPlays,
      published_from_date: publishedFrom,
      published_to_date: publishedTo,
      transcript_status: transcriptStatus,
      comment_status: commentStatus,
      download_status: downloadStatus,
    };
    Object.entries(values).forEach(([key, value]) => {
      if (value) next.set(key, value);
    });
    setParams(next);
  };
  const load = async () => {
    const info = kinds.find((item) => item.id === kind)!;
    if (remote) {
      const response = await remoteApi.results(info.remote, {
        limit: pageSize,
        offset: (page - 1) * pageSize,
        connection_id: connectionId || undefined,
      });
      let rows = response.data.items.map((row: Row) => ({
        ...((row.payload || {}) as Row),
        _entity_id: row.entity_id,
        _connection_id: row.connection_id,
        _account_label: row.account_label,
        _synced_at: row.synced_at,
      }));
      if (q)
        rows = rows.filter((row: Row) =>
          JSON.stringify(row).toLowerCase().includes(q.toLowerCase()),
        );
      if (creator)
        rows = rows.filter(
          (row: Row) => String(row.creator_hash || "") === creator,
        );
      if (source)
        rows = rows.filter((row: Row) =>
          String(row.source_topic || row.source_keyword || "").includes(source),
        );
      setItems(rows);
      setTotal(Number(response.data.total || rows.length));
      return;
    }
    if (kind === "awemes") {
      const response = await libraryApi.awemes(q, {
        ...queryFilters,
        limit: pageSize,
        offset: (page - 1) * pageSize,
      });
      setItems(response.data.items as unknown as Row[]);
      setTotal(response.data.total);
    } else {
      const response = await libraryApi[kind](q);
      setItems(response.data.items);
      setTotal(response.data.total ?? response.data.items.length);
    }
  };
  useEffect(() => {
    if (!capabilities.data) return;
    void load();
    if (!remote)
      libraryApi
        .stats()
        .then((response) => setStats(response.data.counts))
        .catch(() => setStats({}));
  }, [kind, page, remote, capabilities.data, paramsKey, connectionId]);
  useEffect(() => {
    if (remote)
      remoteApi
        .connections()
        .then((response) =>
          setConnections(
            response.data.items.filter(
              (item: Row) => item.status !== "disconnected",
            ),
          ),
        )
        .catch(() => setConnections([]));
  }, [remote]);

  const exportFilters = { ...queryFilters };
  return (
    <div>
      <PageHeader
        eyebrow="数据"
        title="内容库"
        description="搜索和浏览作品、账号、话题、评论与字幕。筛选与页码保留在地址中，可以直接分享当前视图。"
        actions={
          !remote && kind === "awemes" ? (
            <div className="flex gap-2">
              <Button asChild variant="outline">
                <a href={libraryApi.exportUrl("jsonl", q, exportFilters)}>
                  <Download />
                  JSONL
                </a>
              </Button>
              <Button asChild variant="outline">
                <a href={libraryApi.exportUrl("csv", q, exportFilters)}>CSV</a>
              </Button>
            </div>
          ) : undefined
        }
      />
      {!remote ? (
        <div className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-4 xl:grid-cols-7">
          {Object.entries(countLabels).map(([key, label]) => (
            <Surface key={key} className="p-4">
              <p className="text-xs text-slate-500">{label}</p>
              <p className="mt-2 text-xl font-semibold tabular-nums text-slate-950">
                {stats[key] ?? 0}
              </p>
            </Surface>
          ))}
        </div>
      ) : null}
      <Surface className="overflow-hidden">
        <div className="border-b border-slate-200 p-4">
          <div className="flex gap-2 overflow-x-auto pb-1">
            {kinds.map((item) => (
              <button
                key={item.id}
                onClick={() => commitUrl(1, item.id)}
                className={`inline-flex min-h-11 items-center gap-2 whitespace-nowrap rounded-lg px-3 text-sm font-medium ${kind === item.id ? "bg-slate-950 text-white" : "text-slate-600 hover:bg-slate-100"}`}
              >
                <item.icon className="h-4 w-4" />
                {item.label}
              </button>
            ))}
          </div>
          <div className="mt-4 flex flex-col gap-2 lg:flex-row">
            {remote && connections.length > 1 ? (
              <select
                aria-label="按抖音账号筛选"
                value={connectionId}
                onChange={(event) => {
                  const value = event.target.value;
                  setConnectionId(value);
                  commitUrl(1, kind, { connection_id: value });
                }}
                className="h-11 rounded-lg border border-slate-300 bg-white px-3 text-sm"
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
              </select>
            ) : null}
            <label className="relative min-w-0 flex-1">
              <span className="sr-only">全文搜索</span>
              <Search className="absolute left-3 top-3.5 h-4 w-4 text-slate-400" />
              <Input
                name="library-search"
                autoComplete="off"
                value={q}
                onChange={(event) => setQ(event.target.value)}
                onKeyDown={(event) => event.key === "Enter" && commitUrl(1)}
                className="pl-9"
                placeholder="搜索文案、字幕或评论…"
              />
            </label>
            {kind === "awemes" ? (
              <Button
                variant="outline"
                onClick={() => setShowFilters(!showFilters)}
              >
                <Filter />
                筛选条件
                <ChevronDown
                  className={`transition-transform ${showFilters ? "rotate-180" : ""}`}
                />
              </Button>
            ) : null}
            <Button onClick={() => commitUrl(1)}>搜索</Button>
          </div>
          {kind === "awemes" && showFilters ? (
            <div className="mt-4 grid gap-3 rounded-xl bg-slate-50 p-4 sm:grid-cols-2 xl:grid-cols-4">
              <FilterField label="作者 hash">
                <Input
                  name="creator_hash"
                  autoComplete="off"
                  value={creator}
                  onChange={(event) => setCreator(event.target.value)}
                  placeholder="输入脱敏作者标识…"
                />
              </FilterField>
              <FilterField label="来源关键词或话题">
                <Input
                  name="source"
                  autoComplete="off"
                  value={source}
                  onChange={(event) => setSource(event.target.value)}
                  placeholder="输入来源…"
                />
              </FilterField>
              <FilterField label="点赞范围">
                <div className="grid grid-cols-2 gap-2">
                  <Input
                    aria-label="最低点赞"
                    type="number"
                    inputMode="numeric"
                    value={minLikes}
                    onChange={(event) => setMinLikes(event.target.value)}
                    placeholder="最低"
                  />
                  <Input
                    aria-label="最高点赞"
                    type="number"
                    inputMode="numeric"
                    value={maxLikes}
                    onChange={(event) => setMaxLikes(event.target.value)}
                    placeholder="最高"
                  />
                </div>
              </FilterField>
              <FilterField label="最低评论">
                <Input
                  type="number"
                  inputMode="numeric"
                  value={minComments}
                  onChange={(event) => setMinComments(event.target.value)}
                  placeholder="例如 10…"
                />
              </FilterField>
              <FilterField label="最低播放">
                <Input
                  type="number"
                  inputMode="numeric"
                  value={minPlays}
                  onChange={(event) => setMinPlays(event.target.value)}
                  placeholder="例如 1000…"
                />
              </FilterField>
              <FilterField label="发布时间">
                <div className="grid grid-cols-2 gap-2">
                  <Input
                    aria-label="开始日期"
                    type="date"
                    value={publishedFrom}
                    onChange={(event) => setPublishedFrom(event.target.value)}
                  />
                  <Input
                    aria-label="结束日期"
                    type="date"
                    value={publishedTo}
                    onChange={(event) => setPublishedTo(event.target.value)}
                  />
                </div>
              </FilterField>
              <FilterField label="字幕状态">
                <select
                  value={transcriptStatus}
                  onChange={(event) => setTranscriptStatus(event.target.value)}
                  className="h-11 w-full rounded-lg border border-slate-300 bg-white px-3 text-sm"
                >
                  <option value="">全部</option>
                  <option value="native_completed">原生字幕</option>
                  <option value="asr_completed">ASR 字幕</option>
                  <option value="failed">处理失败</option>
                </select>
              </FilterField>
              <FilterField label="处理状态">
                <div className="grid grid-cols-2 gap-2">
                  <select
                    aria-label="评论状态"
                    value={commentStatus}
                    onChange={(event) => setCommentStatus(event.target.value)}
                    className="h-11 rounded-lg border border-slate-300 bg-white px-2 text-sm"
                  >
                    <option value="">评论全部</option>
                    <option value="completed">有评论</option>
                    <option value="empty">无评论</option>
                  </select>
                  <select
                    aria-label="下载状态"
                    value={downloadStatus}
                    onChange={(event) => setDownloadStatus(event.target.value)}
                    className="h-11 rounded-lg border border-slate-300 bg-white px-2 text-sm"
                  >
                    <option value="">下载全部</option>
                    <option value="completed">已下载</option>
                    <option value="failed">失败</option>
                    <option value="deleted">已删除</option>
                  </select>
                </div>
              </FilterField>
            </div>
          ) : null}
        </div>
        {items.length ? (
          <div>
            {kind === "awemes" ? (
              <AwemeList items={items} />
            ) : (
              <GenericList kind={kind} items={items} />
            )}
            <div className="flex items-center justify-between border-t border-slate-200 px-4 py-3">
              <p className="text-xs text-slate-500">共 {total} 条</p>
              <div className="flex gap-2">
                <Button
                  size="sm"
                  variant="outline"
                  disabled={page === 1}
                  onClick={() => commitUrl(page - 1)}
                >
                  <ChevronLeft />
                  上一页
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={page * pageSize >= total}
                  onClick={() => commitUrl(page + 1)}
                >
                  下一页
                  <ChevronRight />
                </Button>
              </div>
            </div>
          </div>
        ) : (
          <EmptyState
            icon={<Library />}
            title="还没有内容"
            description="完成采集后，作品、评论和字幕会出现在这里。"
            action={
              <Button asChild>
                <Link to="/crawl/new">新建采集</Link>
              </Button>
            }
          />
        )}
      </Surface>
    </div>
  );
}

function AwemeList({ items }: { items: Row[] }) {
  return (
    <div>
      {items.map((item, index) => {
        const id = String(item.aweme_id || item._entity_id || index);
        return (
          <article
            key={id}
            className="grid gap-4 border-b border-slate-100 p-4 last:border-0 sm:grid-cols-[112px_1fr] lg:grid-cols-[112px_minmax(260px,1.4fr)_150px_180px_150px]"
          >
            <div className="aspect-video overflow-hidden rounded-lg bg-slate-100 sm:aspect-[4/3]">
              {item.cover_url ? (
                <img
                  src={String(item.cover_url)}
                  width="112"
                  height="84"
                  className="h-full w-full object-cover"
                  alt=""
                  loading="lazy"
                />
              ) : (
                <div className="grid h-full place-items-center">
                  <FileText className="h-6 w-6 text-slate-300" />
                </div>
              )}
            </div>
            <div className="min-w-0">
              <Link
                to={`/library/awemes/${id}`}
                className="line-clamp-2 text-sm font-semibold leading-6 text-slate-950 hover:text-teal-800"
              >
                {String(item.title || item.desc || "未命名作品")}
              </Link>
              <p className="mt-1 truncate text-xs text-slate-500">
                {String(
                  item._account_label ||
                    item.nickname ||
                    item.masked_nickname ||
                    item.creator_hash ||
                    "未知账号",
                )}
              </p>
              <div className="mt-3 flex flex-wrap gap-2 lg:hidden">
                <Stat label="赞" value={item.liked_count} />
                <Stat label="评" value={item.comment_count} />
                <Stat label="播放" value={item.play_count} />
              </div>
            </div>
            <div className="hidden text-xs text-slate-500 lg:block">
              <p className="font-medium text-slate-700">
                {String(item.source_topic || item.source_keyword || "直接采集")}
              </p>
              <p className="mt-1">采集来源</p>
            </div>
            <div className="hidden items-center gap-3 lg:flex">
              <Stat label="赞" value={item.liked_count} />
              <Stat label="评" value={item.comment_count} />
              <Stat label="播放" value={item.play_count} />
            </div>
            <div className="hidden text-xs text-slate-500 lg:block">
              <p>
                {formatDate(
                  (item.publish_time ||
                    item.create_time ||
                    item.collected_at ||
                    item._synced_at) as string | number,
                )}
              </p>
              <div className="mt-2">
                <StatusBadge
                  status={String(item.transcript_status || "completed")}
                  label={item.transcript_status ? "字幕已处理" : "详情已采集"}
                />
              </div>
            </div>
          </article>
        );
      })}
    </div>
  );
}
function GenericList({ kind, items }: { kind: Kind; items: Row[] }) {
  return (
    <div className="divide-y divide-slate-100">
      {items.map((item, index) => (
        <article
          key={String(
            item.creator_hash ||
              item.topic_id ||
              item.comment_id ||
              item.aweme_id ||
              item._entity_id ||
              index,
          )}
          className="p-4 sm:px-5"
        >
          <div className="flex items-start justify-between gap-4">
            <div className="min-w-0">
              <p className="line-clamp-3 break-words text-sm font-medium leading-6 text-slate-900">
                {String(
                  item.nickname ||
                    item.masked_nickname ||
                    item.name ||
                    item.content ||
                    item.full_text ||
                    "未命名记录",
                )}
              </p>
              <p className="mt-1 text-xs text-slate-500">
                {kind === "comments"
                  ? `点赞 ${item.like_count ?? 0} · ${Number(item.level) === 2 ? "二级回复" : "一级评论"}`
                  : String(
                      item.status ||
                        item.creator_hash ||
                        item.topic_id ||
                        item.aweme_id ||
                        item._entity_id ||
                        "",
                    )}
              </p>
            </div>
            {item.status ? <StatusBadge status={String(item.status)} /> : null}
          </div>
        </article>
      ))}
    </div>
  );
}
function FilterField({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <label>
      <span className="mb-1.5 block text-xs font-medium text-slate-600">
        {label}
      </span>
      {children}
    </label>
  );
}
function Stat({ label, value }: { label: string; value: unknown }) {
  return (
    <span className="text-xs text-slate-500">
      <strong className="font-semibold tabular-nums text-slate-800">
        {value == null
          ? "—"
          : new Intl.NumberFormat("zh-CN").format(Number(value))}
      </strong>{" "}
      {label}
    </span>
  );
}
