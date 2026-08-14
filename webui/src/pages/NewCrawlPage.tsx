import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  Check,
  ChevronLeft,
  ChevronRight,
  Download,
  FileText,
  MessageSquareText,
  Mic2,
  Search,
  Settings2,
  Tags,
  UserRoundSearch,
  Video,
} from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { PageHeader, Surface } from "@/components/product/Primitives";
import { crawlerApi, remoteApi, systemApi } from "@/lib/api";
import { useCapabilities } from "@/hooks/useProduct";
import { formatBytes } from "@/lib/presentation";

type Mode = "search" | "topic" | "detail" | "creator";
type Item = Record<string, unknown>;
const modes: Array<{
  id: Mode;
  label: string;
  description: string;
  icon: typeof Search;
  placeholder: string;
  example: string;
}> = [
  {
    id: "search",
    label: "关键词",
    description: "搜索公开作品并补取完整详情",
    icon: Search,
    placeholder: "例如：新能源汽车, 智能驾驶",
    example: "可输入多个关键词，用逗号分隔",
  },
  {
    id: "topic",
    label: "真实话题",
    description: "使用真实话题页，而不是关键词替代",
    icon: Tags,
    placeholder: "话题名称、URL 或话题 ID",
    example: "例如：人工智能 或抖音话题页链接",
  },
  {
    id: "detail",
    label: "指定视频",
    description: "采集一个或多个指定作品",
    icon: Video,
    placeholder: "抖音视频 URL 或作品 ID",
    example: "多个链接或 ID 用逗号分隔",
  },
  {
    id: "creator",
    label: "指定账号",
    description: "采集账号公开资料和作品",
    icon: UserRoundSearch,
    placeholder: "抖音账号主页 URL 或账号 ID",
    example: "支持增量采集新作品",
  },
];

export default function NewCrawlPage() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const capabilities = useCapabilities();
  const [step, setStep] = useState(1),
    [mode, setMode] = useState<Mode>((params.get("mode") as Mode) || "search"),
    [source, setSource] = useState("");
  const [connections, setConnections] = useState<Item[]>([]),
    [connectionId, setConnectionId] = useState("");
  const [notes, setNotes] = useState(30),
    [commentsLimit, setCommentsLimit] = useState(0),
    [incremental, setIncremental] = useState(false);
  const [creatorProfile, setCreatorProfile] = useState(true),
    [comments, setComments] = useState(true),
    [subComments, setSubComments] = useState(true),
    [nativeSubtitle, setNativeSubtitle] = useState(true),
    [asr, setAsr] = useState(true);
  const [downloadMedia, setDownloadMedia] = useState(false),
    [downloadVideo, setDownloadVideo] = useState(true),
    [downloadImages, setDownloadImages] = useState(true),
    [downloadCover, setDownloadCover] = useState(true),
    [downloadMusic, setDownloadMusic] = useState(false),
    [maxDownloads, setMaxDownloads] = useState(5),
    [quotaGb, setQuotaGb] = useState(5);
  const [advanced, setAdvanced] = useState(false),
    [asrModel, setAsrModel] = useState("small"),
    [language, setLanguage] = useState("zh"),
    [stopExisting, setStopExisting] = useState(5),
    [saving, setSaving] = useState(false),
    [storage, setStorage] = useState<Item | null>(null);
  const remote = capabilities.data?.features.remote_worker;
  useEffect(() => {
    systemApi.storage().then((response) => setStorage(response.data));
    if (remote)
      remoteApi
        .connections()
        .then((response) => {
          const list = response.data.items.filter(
            (item: Item) => item.status === "connected",
          );
          setConnections(list);
          if (list[0]) setConnectionId(String(list[0].connection_id));
        })
        .catch(() => undefined);
  }, [remote]);
  const currentMode = modes.find((item) => item.id === mode)!;
  const canContinue = step === 1 ? source.trim().length > 0 : true;
  const payload = useMemo(
    () => ({
      crawler_type: mode,
      keywords: mode === "search" ? source : "",
      topics: mode === "topic" ? source : "",
      specified_ids: mode === "detail" ? source : "",
      creator_ids: mode === "creator" ? source : "",
      max_notes_count: notes,
      enable_comments: comments,
      enable_sub_comments: comments && subComments,
      max_comments_count: comments ? commentsLimit : 0,
      enable_creator_profile: creatorProfile,
      enable_native_subtitle: nativeSubtitle || asr,
      enable_asr: asr,
      asr_model: asrModel,
      asr_language: language,
      download_media: downloadMedia,
      download_video: downloadVideo,
      download_images: downloadImages,
      download_cover: downloadCover,
      download_music: downloadMusic,
      media_quality: "best_h264",
      max_media_downloads: downloadMedia ? maxDownloads : 0,
      max_media_total_bytes: quotaGb * 1024 ** 3,
      media_library_max_bytes: 20 * 1024 ** 3,
      min_free_disk_bytes: 10 * 1024 ** 3,
      skip_existing_media: true,
      verify_media: true,
      keep_asr_source_media: false,
      incremental: mode === "creator" || mode === "topic" ? incremental : false,
      stop_after_existing: stopExisting,
      refresh_existing_metrics: true,
      refresh_existing_comments: false,
    }),
    [
      mode,
      source,
      notes,
      comments,
      subComments,
      commentsLimit,
      creatorProfile,
      nativeSubtitle,
      asr,
      asrModel,
      language,
      downloadMedia,
      downloadVideo,
      downloadImages,
      downloadCover,
      downloadMusic,
      maxDownloads,
      quotaGb,
      incremental,
      stopExisting,
    ],
  );
  const submit = async () => {
    if (!source.trim()) return;
    if (remote && !connectionId) {
      toast.error("请先选择一个已连接的抖音账号");
      return;
    }
    setSaving(true);
    try {
      const response = remote
        ? await remoteApi.createRun({ connection_id: connectionId, ...payload })
        : await crawlerApi.start({
            platform: "dy",
            login_type: "qrcode",
            start_page: 1,
            save_option: "jsonl",
            cookies: "",
            headless: false,
            ...payload,
          });
      toast.success("任务已进入队列");
      navigate(`/tasks/${response.data.run_id}`);
    } catch {
      toast.error("任务创建失败，请检查输入与账号状态");
    } finally {
      setSaving(false);
    }
  };
  return (
    <div>
      <PageHeader
        eyebrow="新建采集"
        title="创建抖音采集任务"
        description="用三个步骤选择来源、采集内容和数量。技术参数已收进高级设置。"
      />
      <div className="mb-6 flex items-center justify-center">
        <div className="flex w-full max-w-2xl items-center">
          {["采集来源", "采集内容", "数量与确认"].map((label, index) => (
            <div key={label} className="contents">
              <div className="flex min-w-24 flex-col items-center gap-2">
                <span
                  className={`grid h-8 w-8 place-items-center rounded-full text-sm font-semibold ${step > index + 1 ? "bg-teal-700 text-white" : step === index + 1 ? "bg-teal-50 text-teal-800 ring-2 ring-teal-700" : "bg-slate-100 text-slate-500"}`}
                >
                  {step > index + 1 ? <Check className="h-4 w-4" /> : index + 1}
                </span>
                <span
                  className={`text-xs font-medium ${step === index + 1 ? "text-slate-950" : "text-slate-500"}`}
                >
                  {label}
                </span>
              </div>
              {index < 2 ? (
                <div
                  className={`mb-6 h-px flex-1 ${step > index + 1 ? "bg-teal-700" : "bg-slate-200"}`}
                />
              ) : null}
            </div>
          ))}
        </div>
      </div>
      <Surface className="mx-auto max-w-5xl overflow-hidden">
        <div className="min-h-[440px] p-5 sm:p-8">
          {step === 1 ? (
            <div>
              <h2 className="text-lg font-semibold text-slate-950">
                选择采集来源
              </h2>
              <p className="mt-1 text-sm text-slate-500">
                每个任务使用一种来源，结果会保留来源关键词或话题。
              </p>
              <div className="mt-6 grid gap-3 sm:grid-cols-2">
                {modes.map((item) => (
                  <button
                    key={item.id}
                    onClick={() => setMode(item.id)}
                    className={`flex min-h-28 items-start gap-4 rounded-xl border p-4 text-left transition ${mode === item.id ? "border-teal-700 bg-teal-50/60 ring-1 ring-teal-700" : "border-slate-200 hover:border-slate-300 hover:bg-slate-50"}`}
                  >
                    <span
                      className={`grid h-10 w-10 shrink-0 place-items-center rounded-lg ${mode === item.id ? "bg-teal-700 text-white" : "bg-slate-100 text-slate-500"}`}
                    >
                      <item.icon className="h-5 w-5" />
                    </span>
                    <span>
                      <strong className="text-sm text-slate-950">
                        {item.label}
                      </strong>
                      <span className="mt-1 block text-xs leading-5 text-slate-500">
                        {item.description}
                      </span>
                    </span>
                  </button>
                ))}
              </div>
              <div className="mt-6">
                <label
                  htmlFor="crawl-source"
                  className="text-sm font-medium text-slate-800"
                >
                  {currentMode.label}内容
                </label>
                <Input
                  id="crawl-source"
                  className="mt-2 h-12"
                  value={source}
                  onChange={(event) => setSource(event.target.value)}
                  placeholder={currentMode.placeholder}
                  aria-describedby="source-help"
                />
                <p id="source-help" className="mt-2 text-xs text-slate-500">
                  {currentMode.example}
                </p>
              </div>
            </div>
          ) : null}
          {step === 2 ? (
            <div>
              <h2 className="text-lg font-semibold text-slate-950">
                选择采集内容
              </h2>
              <p className="mt-1 text-sm text-slate-500">
                作品详情始终采集，其他内容可以按需要关闭。
              </p>
              <div className="mt-6 divide-y divide-slate-100 rounded-xl border border-slate-200">
                {(
                  [
                    [
                      "作品详情",
                      "文案、互动量、媒体元数据",
                      true,
                      () => undefined,
                      FileText,
                      true,
                    ],
                    [
                      "账号公开资料",
                      "脱敏昵称、简介、认证与公开指标",
                      creatorProfile,
                      setCreatorProfile,
                      UserRoundSearch,
                      false,
                    ],
                    [
                      "一级评论",
                      "采集接口可见的一级评论",
                      comments,
                      setComments,
                      MessageSquareText,
                      false,
                    ],
                    [
                      "二级回复",
                      "采集一级评论下可见的回复",
                      subComments,
                      setSubComments,
                      MessageSquareText,
                      !comments,
                    ],
                    [
                      "原生字幕",
                      "优先读取平台提供的字幕",
                      nativeSubtitle,
                      setNativeSubtitle,
                      FileText,
                      false,
                    ],
                    [
                      "本地 ASR",
                      "无原生字幕时在抓取设备本地转写",
                      asr,
                      setAsr,
                      Mic2,
                      false,
                    ],
                  ] as Array<
                    [
                      string,
                      string,
                      boolean,
                      (value: boolean) => void,
                      typeof FileText,
                      boolean,
                    ]
                  >
                ).map(([label, desc, checked, setter, Icon, disabled]) => (
                  <ToggleRow
                    key={label}
                    label={label}
                    description={desc}
                    checked={checked}
                    disabled={disabled}
                    icon={Icon}
                    onChange={setter}
                  />
                ))}
              </div>
              <div className="mt-4 rounded-xl border border-slate-200">
                <ToggleRow
                  label="正式媒体下载"
                  description="将视频、图文和封面永久保存在抓取设备"
                  checked={downloadMedia}
                  icon={Download}
                  onChange={setDownloadMedia}
                />
                {downloadMedia ? (
                  <div className="grid gap-3 border-t border-slate-100 bg-slate-50 p-4 sm:grid-cols-4">
                    {(
                      [
                        ["视频", downloadVideo, setDownloadVideo],
                        ["图文", downloadImages, setDownloadImages],
                        ["封面", downloadCover, setDownloadCover],
                        ["音乐", downloadMusic, setDownloadMusic],
                      ] as Array<[string, boolean, (value: boolean) => void]>
                    ).map(([label, value, setter]) => (
                      <label
                        key={label}
                        className="flex min-h-11 cursor-pointer items-center gap-2 rounded-lg bg-white px-3 text-sm ring-1 ring-slate-200"
                      >
                        <input
                          type="checkbox"
                          checked={value}
                          onChange={(event) => setter(event.target.checked)}
                          className="accent-teal-700"
                        />
                        {label}
                      </label>
                    ))}
                  </div>
                ) : null}
              </div>
            </div>
          ) : null}
          {step === 3 ? (
            <div>
              <h2 className="text-lg font-semibold text-slate-950">
                数量与确认
              </h2>
              <p className="mt-1 text-sm text-slate-500">
                核对采集范围。达到下载配额后仍会继续保存作品详情。
              </p>
              <div className="mt-6 grid gap-5 lg:grid-cols-[1fr_.9fr]">
                <div className="space-y-4">
                  <Field
                    label="最大采集作品数"
                    hint="搜索、话题和账号分页都不会超过此数量"
                  >
                    <Input
                      type="number"
                      min={1}
                      max={10000}
                      value={notes}
                      onChange={(event) =>
                        setNotes(Math.max(1, Number(event.target.value)))
                      }
                    />
                  </Field>
                  {comments ? (
                    <Field
                      label="评论数量"
                      hint="0 表示采集接口可见的全部一级、二级评论"
                    >
                      <Input
                        type="number"
                        min={0}
                        max={10000}
                        value={commentsLimit}
                        onChange={(event) =>
                          setCommentsLimit(
                            Math.max(0, Number(event.target.value)),
                          )
                        }
                      />
                    </Field>
                  ) : null}
                  {downloadMedia ? (
                    <div className="grid grid-cols-2 gap-3">
                      <Field label="最多下载作品">
                        <Input
                          type="number"
                          min={0}
                          value={maxDownloads}
                          onChange={(event) =>
                            setMaxDownloads(
                              Math.max(0, Number(event.target.value)),
                            )
                          }
                        />
                      </Field>
                      <Field label="单任务配额（GB）">
                        <Input
                          type="number"
                          min={1}
                          value={quotaGb}
                          onChange={(event) =>
                            setQuotaGb(Math.max(1, Number(event.target.value)))
                          }
                        />
                      </Field>
                    </div>
                  ) : null}
                  {mode === "creator" || mode === "topic" ? (
                    <ToggleRow
                      label="增量采集"
                      description="连续遇到已处理旧作品后提前停止"
                      checked={incremental}
                      onChange={setIncremental}
                      icon={ChevronRight}
                    />
                  ) : null}
                  <button
                    onClick={() => setAdvanced(!advanced)}
                    className="flex min-h-11 w-full items-center justify-between rounded-lg border border-slate-200 px-4 text-sm font-medium text-slate-700 hover:bg-slate-50"
                  >
                    <span className="flex items-center gap-2">
                      <Settings2 className="h-4 w-4" />
                      高级设置
                    </span>
                    <ChevronRight
                      className={`h-4 w-4 transition ${advanced ? "rotate-90" : ""}`}
                    />
                  </button>
                  {advanced ? (
                    <div className="grid gap-3 rounded-lg bg-slate-50 p-4 sm:grid-cols-3">
                      <Field label="ASR 模型">
                        <Input
                          value={asrModel}
                          onChange={(event) => setAsrModel(event.target.value)}
                        />
                      </Field>
                      <Field label="语言">
                        <Input
                          value={language}
                          onChange={(event) => setLanguage(event.target.value)}
                        />
                      </Field>
                      <Field label="连续旧作品停止数">
                        <Input
                          type="number"
                          min={1}
                          value={stopExisting}
                          onChange={(event) =>
                            setStopExisting(
                              Math.max(1, Number(event.target.value)),
                            )
                          }
                        />
                      </Field>
                    </div>
                  ) : null}
                </div>
                <aside className="rounded-xl bg-slate-950 p-5 text-slate-100">
                  <p className="text-xs font-semibold uppercase tracking-wider text-teal-300">
                    配置摘要
                  </p>
                  <dl className="mt-5 space-y-4 text-sm">
                    <Summary
                      label="采集来源"
                      value={`${currentMode.label}：${source}`}
                    />
                    <Summary
                      label="运行账号"
                      value={
                        remote
                          ? accountLabel(
                              connections.find(
                                (item) => item.connection_id === connectionId,
                              ),
                              "请选择账号",
                            )
                          : "本机 Chrome 登录账号"
                      }
                    />
                    <Summary
                      label="采集范围"
                      value={`最多 ${notes} 个作品${comments ? "，包含评论" : ""}${asr ? "，ASR 兜底" : ""}`}
                    />
                    <Summary
                      label="媒体下载"
                      value={
                        downloadMedia
                          ? `最多 ${maxDownloads} 个作品 / ${quotaGb} GB`
                          : "关闭"
                      }
                    />
                    <Summary
                      label="磁盘剩余"
                      value={formatBytes(Number(storage?.free_bytes || 0))}
                    />
                  </dl>
                  {remote ? (
                    <div className="mt-5">
                      <label className="text-xs text-slate-400">运行账号</label>
                      <select
                        value={connectionId}
                        onChange={(event) =>
                          setConnectionId(event.target.value)
                        }
                        className="mt-2 h-11 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 text-sm text-white"
                      >
                        <option value="">选择已连接账号</option>
                        {connections.map((item) => (
                          <option
                            key={String(item.connection_id)}
                            value={String(item.connection_id)}
                          >
                            {accountLabel(item)}
                          </option>
                        ))}
                      </select>
                    </div>
                  ) : null}
                </aside>
              </div>
            </div>
          ) : null}
        </div>
        <div className="sticky bottom-0 flex items-center justify-between border-t border-slate-200 bg-white px-5 py-4 sm:px-8">
          <Button
            variant="ghost"
            disabled={step === 1}
            onClick={() => setStep((value) => value - 1)}
          >
            <ChevronLeft />
            上一步
          </Button>
          {step < 3 ? (
            <Button
              disabled={!canContinue}
              onClick={() => setStep((value) => value + 1)}
            >
              下一步
              <ChevronRight />
            </Button>
          ) : (
            <Button
              size="lg"
              disabled={saving || (Boolean(remote) && !connectionId)}
              onClick={submit}
            >
              {saving ? "正在创建…" : "开始采集"}
            </Button>
          )}
        </div>
      </Surface>
    </div>
  );
}

function ToggleRow({
  label,
  description,
  checked,
  onChange,
  icon: Icon,
  disabled = false,
}: {
  label: string;
  description: string;
  checked: boolean;
  onChange: (value: boolean) => void;
  icon: typeof FileText;
  disabled?: boolean;
}) {
  return (
    <div
      className={`flex min-h-[76px] items-center gap-4 px-4 py-3 ${disabled ? "opacity-50" : ""}`}
    >
      <span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-slate-100 text-slate-600">
        <Icon className="h-4 w-4" />
      </span>
      <span id={`switch-${label}`} className="min-w-0 flex-1">
        <span className="block text-sm font-medium text-slate-900">
          {label}
        </span>
        <span className="mt-0.5 block text-xs leading-5 text-slate-500">
          {description}
        </span>
      </span>
      <button
        type="button"
        role="switch"
        aria-labelledby={`switch-${label}`}
        aria-checked={checked}
        disabled={disabled}
        onClick={() => !disabled && onChange(!checked)}
        className={`relative h-11 w-14 shrink-0 touch-manipulation rounded-full transition-colors ${checked ? "bg-teal-700" : "bg-slate-300"}`}
      >
        <span
          className={`absolute top-2.5 h-6 w-6 rounded-full bg-white shadow transition-transform ${checked ? "translate-x-6" : "translate-x-1"}`}
        />
      </button>
    </div>
  );
}
function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className="text-sm font-medium text-slate-800">{label}</span>
      {hint ? (
        <span className="mt-1 block text-xs text-slate-500">{hint}</span>
      ) : null}
      <span className="mt-2 block">{children}</span>
    </label>
  );
}
function Summary({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs text-slate-400">{label}</dt>
      <dd className="mt-1 break-words leading-6 text-slate-100">{value}</dd>
    </div>
  );
}

function accountLabel(item?: Item, fallback = "抖音账号") {
  return String(
    item?.display_name || item?.remark || item?.masked_nickname || fallback,
  );
}
