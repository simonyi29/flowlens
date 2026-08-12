import type { ComponentType, ReactNode, KeyboardEvent } from 'react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Database, Globe, KeyRound, MessageSquare, Play, Square, X } from 'lucide-react'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Input } from '@/components/ui/input'
import { Checkbox } from '@/components/ui/checkbox'
import { Button } from '@/components/ui/button'
import { useCrawlerStore } from '@/store/crawlerStore'
import { usePlatforms, useConfigOptions, useStartCrawler, useStopCrawler, useDouyinProgress } from '@/hooks/useCrawler'
import { ParsedIdList } from './ParsedIdList'

type SectionProps = {
  title: string
  description: string
  icon: ComponentType<{ className?: string }>
  children: ReactNode
  className?: string
}

function Section({ title, description, icon: Icon, children, className = '' }: SectionProps) {
  return (
    <section className={`rounded-lg glass-panel float-panel overflow-hidden ${className}`}>
      <header className="px-4 py-3 border-b border-cyber-border-subtle/50 flex items-center gap-3 bg-cyber-bg-tertiary/30">
        <div className="h-8 w-8 rounded-md bg-cyber-bg-tertiary border border-cyber-border-subtle flex items-center justify-center flex-shrink-0">
          <Icon className="h-4 w-4 text-cyber-neon-cyan" />
        </div>
        <div className="min-w-0">
          <div className="text-xs font-mono font-semibold text-cyber-text-primary tracking-wide">
            {title}
          </div>
          <div className="text-[10px] text-cyber-text-muted leading-snug truncate">
            {description}
          </div>
        </div>
      </header>
      <div className="p-4 space-y-4">
        {children}
      </div>
    </section>
  )
}

type FieldProps = {
  label: string
  hint?: string
  children: ReactNode
}

function Field({ label, hint, children }: FieldProps) {
  return (
    <div className="space-y-2">
      <div className="space-y-0.5">
        <Label className="text-xs text-cyber-text-secondary font-mono">
          {label}
        </Label>
        {hint ? (
          <p className="text-[10px] text-cyber-text-muted leading-snug">
            {hint}
          </p>
        ) : null}
      </div>
      {children}
    </div>
  )
}

type KeywordInputProps = {
  value: string
  onChange: (value: string) => void
  placeholder?: string
  disabled?: boolean
}

function KeywordInput({ value, onChange, placeholder, disabled }: KeywordInputProps) {
  const [inputValue, setInputValue] = useState('')

  // 将逗号分隔的字符串转换为数组
  const keywords = value ? value.split(',').map((k) => k.trim()).filter(Boolean) : []

  const handleKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      e.preventDefault()
      const trimmed = inputValue.trim()
      if (trimmed && !keywords.includes(trimmed)) {
        const newKeywords = [...keywords, trimmed]
        onChange(newKeywords.join(','))
        setInputValue('')
      }
    }
  }

  const removeKeyword = (keywordToRemove: string) => {
    const newKeywords = keywords.filter((k) => k !== keywordToRemove)
    onChange(newKeywords.join(','))
  }

  return (
    <div className="space-y-2">
      <Input
        value={inputValue}
        onChange={(e) => setInputValue(e.target.value)}
        onKeyDown={handleKeyDown}
        placeholder={placeholder}
        disabled={disabled}
        className="h-9 text-xs"
      />
      {keywords.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {keywords.map((keyword) => (
            <span
              key={keyword}
              className="inline-flex items-center gap-1 px-2 py-1 rounded-md bg-cyber-neon-cyan/10 border border-cyber-neon-cyan/30 text-cyber-neon-cyan text-xs font-mono"
            >
              {keyword}
              {!disabled && (
                <button
                  type="button"
                  onClick={() => removeKeyword(keyword)}
                  className="hover:text-cyber-neon-pink transition-colors"
                >
                  <X className="w-3 h-3" />
                </button>
              )}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

export function CrawlerConfigPanel() {
  const { t } = useTranslation('config')
  const config = useCrawlerStore((state) => state.config)
  const updateConfig = useCrawlerStore((state) => state.updateConfig)
  const status = useCrawlerStore((state) => state.status)

  const { data: platforms } = usePlatforms()
  const { data: options } = useConfigOptions()
  const { mutate: startCrawler, isPending: isStarting } = useStartCrawler()
  const { mutate: stopCrawler, isPending: isStopping } = useStopCrawler()
  const { data: progressItems = [] } = useDouyinProgress(config.platform === 'dy')

  const isDisabled = status === 'running' || status === 'stopping'
  const isRunning = status === 'running'
  const isBusy = isStarting || isStopping || status === 'stopping'

  const handleStart = () => {
    if (config.platform === 'dy') {
      startCrawler(config)
      return
    }
    const {
      topics: _topics,
      enable_creator_profile: _creatorProfile,
      force_creator_refresh: _forceCreatorRefresh,
      enable_native_subtitle: _nativeSubtitle,
      enable_asr: _asr,
      asr_model: _asrModel,
      asr_language: _asrLanguage,
      save_raw_payload: _rawPayload,
      keep_media: _keepMedia,
      download_media: _downloadMedia,
      download_video: _downloadVideo,
      download_images: _downloadImages,
      download_cover: _downloadCover,
      download_music: _downloadMusic,
      media_quality: _mediaQuality,
      max_media_downloads: _maxMediaDownloads,
      max_media_total_bytes: _maxMediaTotalBytes,
      media_library_max_bytes: _mediaLibraryMaxBytes,
      min_free_disk_bytes: _minFreeDiskBytes,
      skip_existing_media: _skipExistingMedia,
      verify_media: _verifyMedia,
      keep_asr_source_media: _keepAsrSourceMedia,
      incremental: _incremental,
      stop_after_existing: _stopAfterExisting,
      refresh_existing_metrics: _refreshExistingMetrics,
      refresh_existing_comments: _refreshExistingComments,
      schedule_id: _scheduleId,
      ...commonConfig
    } = config
    startCrawler(commonConfig as typeof config)
  }

  const handleStop = () => {
    stopCrawler()
  }

  return (
    <div className="space-y-4 animate-slide-up">
      {/* Row 1: Three Config Columns */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {/* Column 1: Target & Mode Section */}
        <Section
          title={t('section.targetMatrix.title')}
          description={t('section.targetMatrix.description')}
          icon={Globe}
        >
          <Field label={t('field.platform')}>
            <Select
              value={config.platform}
              onValueChange={(value) => updateConfig({
                platform: value,
                crawler_type: config.crawler_type === 'topic' && value !== 'dy' ? 'search' : config.crawler_type,
                save_option: value === 'dy' && !['jsonl', 'sqlite'].includes(config.save_option) ? 'jsonl' : config.save_option,
              })}
              disabled={isDisabled}
            >
              <SelectTrigger className="h-9 text-xs">
                <SelectValue placeholder={t('field.platformPlaceholder')} />
              </SelectTrigger>
              <SelectContent>
                {platforms?.map((platform) => (
                  <SelectItem key={platform.value} value={platform.value}>
                    {platform.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>

          <div className="grid grid-cols-2 gap-3">
            <Field label={t('field.crawlType')}>
              <Select
                value={config.crawler_type}
                onValueChange={(value) => updateConfig({ crawler_type: value })}
                disabled={isDisabled}
              >
                <SelectTrigger className="h-9 text-xs">
                  <SelectValue placeholder={t('field.crawlTypePlaceholder')} />
                </SelectTrigger>
                <SelectContent>
                  {options?.crawler_types.filter((type) => type.value !== 'topic' || config.platform === 'dy').map((type) => (
                    <SelectItem key={type.value} value={type.value}>
                      {type.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>

            <Field label={t('field.startPage')}>
              <Input
                type="number"
                min={1}
                value={config.start_page}
                onChange={(e) => updateConfig({ start_page: parseInt(e.target.value) || 1 })}
                disabled={isDisabled}
                className="h-9 text-xs"
              />
            </Field>
          </div>

          {/* 根据爬虫类型显示不同的输入框 */}
          {config.crawler_type === 'search' && (
            <Field label={t('field.keywords')} hint={t('field.keywordsHint')}>
              <KeywordInput
                placeholder={t('field.keywordsPlaceholder')}
                value={config.keywords}
                onChange={(keywords) => updateConfig({ keywords })}
                disabled={isDisabled}
              />
            </Field>
          )}

          {config.platform === 'dy' && config.crawler_type === 'topic' && (
            <Field label={t('field.topics')} hint={t('field.topicsHint')}>
              <textarea
                value={config.topics}
                onChange={(e) => updateConfig({ topics: e.target.value })}
                disabled={isDisabled}
                placeholder={t('field.topicsPlaceholder')}
                className="min-h-[60px] w-full rounded-md border border-cyber-border-DEFAULT bg-cyber-bg-tertiary px-3 py-2 text-xs font-mono text-cyber-text-primary placeholder:text-cyber-text-muted focus-visible:outline-none focus-visible:border-cyber-neon-cyan/50 transition-all resize-none"
              />
            </Field>
          )}

          {config.crawler_type === 'detail' && (
            <Field label={t('field.specifiedIds')} hint={t('field.specifiedIdsHint')}>
              <textarea
                value={config.specified_ids}
                onChange={(e) => updateConfig({ specified_ids: e.target.value })}
                disabled={isDisabled}
                placeholder={t(`field.specifiedIdsPlaceholder.${config.platform}`, t('field.specifiedIdsPlaceholder.default'))}
                className="min-h-[60px] w-full rounded-md border border-cyber-border-DEFAULT bg-cyber-bg-tertiary px-3 py-2 text-xs font-mono text-cyber-text-primary placeholder:text-cyber-text-muted focus-visible:outline-none focus-visible:border-cyber-neon-cyan/50 focus-visible:shadow-cyber-soft disabled:cursor-not-allowed disabled:opacity-50 transition-all resize-none"
              />
              <ParsedIdList
                value={config.specified_ids}
                platform={config.platform}
                type="detail"
                disabled={isDisabled}
              />
              {config.platform === 'xhs' && (
                <div className="mt-2 rounded-lg border border-cyber-neon-orange/30 bg-cyber-neon-orange/5 p-2 text-[10px] leading-snug text-cyber-neon-orange font-mono">
                  {t('warning.xhsToken')}
                </div>
              )}
            </Field>
          )}

          {config.crawler_type === 'creator' && (
            <Field label={t('field.creatorIds')} hint={t('field.creatorIdsHint')}>
              <textarea
                value={config.creator_ids}
                onChange={(e) => updateConfig({ creator_ids: e.target.value })}
                disabled={isDisabled}
                placeholder={t(`field.creatorIdsPlaceholder.${config.platform}`, t('field.creatorIdsPlaceholder.default'))}
                className="min-h-[60px] w-full rounded-md border border-cyber-border-DEFAULT bg-cyber-bg-tertiary px-3 py-2 text-xs font-mono text-cyber-text-primary placeholder:text-cyber-text-muted focus-visible:outline-none focus-visible:border-cyber-neon-cyan/50 focus-visible:shadow-cyber-soft disabled:cursor-not-allowed disabled:opacity-50 transition-all resize-none"
              />
              <ParsedIdList
                value={config.creator_ids}
                platform={config.platform}
                type="creator"
                disabled={isDisabled}
              />
              {config.platform === 'xhs' && (
                <div className="mt-2 rounded-lg border border-cyber-neon-orange/30 bg-cyber-neon-orange/5 p-2 text-[10px] leading-snug text-cyber-neon-orange font-mono">
                  {t('warning.xhsToken')}
                </div>
              )}
            </Field>
          )}
        </Section>

        {/* Column 2: Authentication Section */}
        <Section
          title={t('section.authMatrix.title')}
          description={t('section.authMatrix.description')}
          icon={KeyRound}
        >
          <Field label={t('field.loginMethod')}>
            <Select
              value={config.login_type}
              onValueChange={(value) => updateConfig({ login_type: value })}
              disabled={isDisabled}
            >
              <SelectTrigger className="h-9 text-xs">
                <SelectValue placeholder={t('field.loginMethodPlaceholder')} />
              </SelectTrigger>
              <SelectContent>
                {options?.login_types.map((type) => (
                  <SelectItem key={type.value} value={type.value}>
                    {type.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>

          {config.login_type === 'cookie' ? (
            <Field label={t('field.cookies')} hint={t('field.cookiesHint')}>
              <textarea
                value={config.cookies}
                onChange={(e) => updateConfig({ cookies: e.target.value })}
                disabled={isDisabled}
                placeholder={t('field.cookiesPlaceholder')}
                className="min-h-[80px] w-full rounded-md border border-cyber-border-DEFAULT bg-cyber-bg-tertiary px-3 py-2 text-xs font-mono text-cyber-text-primary placeholder:text-cyber-text-muted focus-visible:outline-none focus-visible:border-cyber-neon-cyan/50 focus-visible:shadow-cyber-soft disabled:cursor-not-allowed disabled:opacity-50 transition-all resize-none"
              />
            </Field>
          ) : null}

          {config.login_type === 'cookie' && (config.platform === 'xhs' || config.platform === 'dy') ? (
            <div className="rounded-lg border border-cyber-neon-orange/30 bg-cyber-neon-orange/5 p-3 text-[11px] leading-snug text-cyber-neon-orange font-mono">
              {t('warning.cookieSlider')}
            </div>
          ) : null}
        </Section>

        {/* Column 3: Output & Runtime Section */}
        <Section
          title={t('section.outputConfig.title')}
          description={t('section.outputConfig.description')}
          icon={Database}
        >
          <Field label={t('field.saveFormat')}>
            <Select
              value={config.save_option}
              onValueChange={(value) => updateConfig({ save_option: value })}
              disabled={isDisabled}
            >
              <SelectTrigger className="h-9 text-xs">
                <SelectValue placeholder={t('field.saveFormatPlaceholder')} />
              </SelectTrigger>
              <SelectContent>
                {options?.save_options.filter((option) => config.platform !== 'dy' || ['jsonl', 'sqlite'].includes(option.value)).map((option) => (
                  <SelectItem key={option.value} value={option.value}>
                    {option.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>

          <div className="space-y-2">
            <div className="flex items-center gap-3 rounded-lg border border-cyber-border-subtle bg-cyber-bg-tertiary/30 p-2.5 hover:border-cyber-border-DEFAULT transition-colors">
              <Checkbox
                checked={config.enable_comments}
                onCheckedChange={(checked) => {
                  const isChecked = checked === true
                  updateConfig({
                    enable_comments: isChecked,
                    enable_sub_comments: isChecked ? config.enable_sub_comments : false,
                  })
                }}
                disabled={isDisabled}
              />
              <div className="flex items-center gap-2">
                <MessageSquare className="h-3.5 w-3.5 text-cyber-text-secondary" />
                <p className="text-xs font-mono text-cyber-text-primary">{t('field.commentExtraction')}</p>
              </div>
            </div>

            <div className="flex items-center gap-3 rounded-lg border border-cyber-border-subtle bg-cyber-bg-tertiary/30 p-2.5 hover:border-cyber-border-DEFAULT transition-colors">
              <Checkbox
                checked={config.enable_sub_comments}
                onCheckedChange={(checked) => updateConfig({ enable_sub_comments: checked === true })}
                disabled={isDisabled || !config.enable_comments}
              />
              <p className="text-xs font-mono text-cyber-text-primary">{t('field.subComments')}</p>
            </div>

            <div className="flex items-center gap-3 rounded-lg border border-cyber-border-subtle bg-cyber-bg-tertiary/30 p-2.5 hover:border-cyber-border-DEFAULT transition-colors">
              <Checkbox
                checked={config.headless}
                onCheckedChange={(checked) => updateConfig({ headless: checked === true })}
                disabled={isDisabled}
              />
              <div className="min-w-0 flex-1">
                <p className="text-xs font-mono text-cyber-text-primary">{t('field.headlessMode')}</p>
                <p className="text-[10px] text-cyber-text-muted leading-snug">
                  {t('field.headlessModeHint')}
                </p>
              </div>
            </div>
          </div>
        </Section>
      </div>

      {config.platform === 'dy' && (
        <Section
          title={t('section.douyinEnhanced.title')}
          description={t('section.douyinEnhanced.description')}
          icon={MessageSquare}
        >
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <Field label={t('field.maxComments')} hint={t('field.maxCommentsHint')}>
              <Input type="number" min={0} value={config.max_comments_count}
                onChange={(e) => updateConfig({ max_comments_count: Math.max(0, Number(e.target.value) || 0) })}
                disabled={isDisabled} className="h-9 text-xs" />
            </Field>
            <Field label={t('field.asrModel')}>
              <Input value={config.asr_model} onChange={(e) => updateConfig({ asr_model: e.target.value })}
                disabled={isDisabled || !config.enable_asr} className="h-9 text-xs" />
            </Field>
            <Field label={t('field.asrLanguage')}>
              <Input value={config.asr_language} onChange={(e) => updateConfig({ asr_language: e.target.value })}
                disabled={isDisabled || !config.enable_asr} className="h-9 text-xs" />
            </Field>
            <Field label="正式媒体最大作品数">
              <Input type="number" min={0} value={config.max_media_downloads}
                onChange={(e) => updateConfig({ max_media_downloads: Math.max(0, Number(e.target.value) || 0) })}
                disabled={isDisabled || !config.download_media} className="h-9 text-xs" />
            </Field>
            <Field label="单任务媒体配额（GB）">
              <Input type="number" min={1} value={Math.round(config.max_media_total_bytes / 1073741824)}
                onChange={(e) => updateConfig({ max_media_total_bytes: Math.max(1, Number(e.target.value) || 1) * 1073741824 })}
                disabled={isDisabled || !config.download_media} className="h-9 text-xs" />
            </Field>
            <Field label="连续旧作品停止数">
              <Input type="number" min={1} value={config.stop_after_existing}
                onChange={(e) => updateConfig({ stop_after_existing: Math.max(1, Number(e.target.value) || 5) })}
                disabled={isDisabled || !config.incremental} className="h-9 text-xs" />
            </Field>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
            {[
              ['enable_creator_profile', t('field.creatorProfile')],
              ['force_creator_refresh', t('field.forceCreatorRefresh')],
              ['enable_native_subtitle', t('field.nativeSubtitle')],
              ['enable_asr', t('field.localAsr')],
              ['save_raw_payload', t('field.rawPayload')],
              ['keep_media', t('field.keepMedia')],
              ['download_media', '永久下载媒体'],
              ['download_video', '下载视频'],
              ['download_images', '下载图文图片'],
              ['download_cover', '下载封面'],
              ['download_music', '下载音乐'],
              ['verify_media', '校验媒体完整性'],
              ['skip_existing_media', '跳过已有媒体'],
              ['keep_asr_source_media', '保留 ASR 源媒体'],
              ['incremental', '增量采集'],
              ['refresh_existing_metrics', '刷新旧作品指标'],
              ['refresh_existing_comments', '刷新旧作品评论'],
            ].map(([key, label]) => (
              <div key={key} className="flex items-center gap-3 rounded-lg border border-cyber-border-subtle bg-cyber-bg-tertiary/30 p-2.5">
                <Checkbox checked={Boolean(config[key as keyof typeof config])}
                  onCheckedChange={(checked) => updateConfig({ [key]: checked === true })}
                  disabled={isDisabled} />
                <p className="text-xs font-mono text-cyber-text-primary">{label}</p>
              </div>
            ))}
          </div>
          <p className="text-[10px] text-cyber-text-muted">{t('field.asrEnvironmentHint')}</p>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <div className="flex items-center gap-3 rounded-lg border border-cyber-border-subtle bg-cyber-bg-tertiary/30 p-2.5">
              <Checkbox checked={config.enable_ip_proxy}
                onCheckedChange={(checked) => updateConfig({ enable_ip_proxy: checked === true })}
                disabled={isDisabled} />
              <p className="text-xs font-mono text-cyber-text-primary">{t('field.staticProxy')}</p>
            </div>
            <Input value={config.static_proxy_url}
              onChange={(e) => updateConfig({ static_proxy_url: e.target.value })}
              disabled={isDisabled || !config.enable_ip_proxy}
              placeholder="http://user:password@host:port" className="h-9 text-xs" />
          </div>
        </Section>
      )}

      {config.platform === 'dy' && progressItems.length > 0 ? (
        <Section
          title="抖音任务状态"
          description="结构化展示最近的采集检查点、数量与失败原因"
          icon={Play}
        >
          <div className="overflow-x-auto rounded-lg border border-cyber-border-subtle">
            <table className="w-full min-w-[720px] text-left text-xs font-mono">
              <thead className="bg-cyber-bg-tertiary/70 text-cyber-text-muted">
                <tr>
                  <th className="px-3 py-2 font-medium">类型</th>
                  <th className="px-3 py-2 font-medium">对象</th>
                  <th className="px-3 py-2 font-medium">状态</th>
                  <th className="px-3 py-2 font-medium">进度</th>
                  <th className="px-3 py-2 font-medium">错误</th>
                </tr>
              </thead>
              <tbody>
                {progressItems.map((item) => (
                  <tr key={`${item.scope}:${item.scope_id}`} className="border-t border-cyber-border-subtle/60">
                    <td className="px-3 py-2 text-cyber-text-secondary">{item.scope}</td>
                    <td className="max-w-[240px] truncate px-3 py-2 text-cyber-text-primary" title={item.scope_id}>
                      {item.scope_id}
                    </td>
                    <td className="px-3 py-2">
                      <span className={item.status === 'complete'
                        ? 'text-emerald-400'
                        : item.status === 'failed' ? 'text-cyber-neon-pink' : 'text-cyber-neon-orange'}>
                        {item.status}
                      </span>
                    </td>
                    <td className="px-3 py-2 text-cyber-text-secondary">
                      {item.collected_count}{item.expected_count == null ? '' : ` / ${item.expected_count}`}
                    </td>
                    <td className="max-w-[320px] truncate px-3 py-2 text-cyber-neon-pink" title={item.last_error}>
                      {item.last_error || '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>
      ) : null}

      {/* Row 2: Start/Stop Button - Full Width */}
      <div className="w-full">
        {isRunning ? (
          <Button
            onClick={handleStop}
            disabled={isBusy}
            className="w-full h-12 bg-cyber-neon-pink text-white font-mono font-bold text-sm tracking-wider hover:bg-cyber-neon-pink/90 hover:shadow-glow-pink-sm transition-all"
          >
            <Square className="w-4 h-4" />
            {isStopping ? t('button.stopping') : t('button.terminate')}
          </Button>
        ) : (
          <Button
            onClick={handleStart}
            disabled={isBusy}
            className="w-full h-12 bg-cyber-neon-cyan text-cyber-bg-primary font-mono font-bold text-sm tracking-wider hover:bg-cyber-neon-cyan/90 hover:shadow-glow-cyan-sm transition-all"
          >
            <Play className="w-4 h-4" />
            {isStarting ? t('button.initiating') : t('button.initiateScan')}
          </Button>
        )}
      </div>
    </div>
  )
}
