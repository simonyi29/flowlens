import { useTranslation } from 'react-i18next'
import { Sparkles, Heart, Github } from 'lucide-react'

export function AuthorFooter() {
  const { t } = useTranslation('license')

  return (
    <footer className="h-24 flex-shrink-0 glass-panel border-t border-cyber-border-subtle">
      <div className="h-full px-6 flex items-center justify-center gap-6">
        {/* Author Avatar */}
        <div className="w-14 h-14 rounded-lg overflow-hidden border-2 border-cyber-neon-cyan/60 flex-shrink-0 shadow-glow-cyan-sm">
          <img
            src="/logos/flowlens-logo.png"
            alt="流镜 FlowLens"
            className="w-full h-full object-cover"
          />
        </div>

        {/* Author Info */}
        <div className="flex flex-col gap-1">
          <div className="flex items-center gap-2">
            <span className="text-lg font-bold text-cyber-text-primary">
              {t('author.name')}
            </span>
            <Sparkles className="w-5 h-5 text-cyber-neon-cyan animate-pulse" />
          </div>
          <span className="text-sm text-cyber-text-muted hidden sm:inline">
            {t('author.description')}
          </span>
          <div className="flex items-center gap-2 text-cyber-neon-cyan">
            <Heart className="w-4 h-4 fill-current animate-pulse" />
            <span className="text-sm font-medium">
              {t('author.slogan')}
            </span>
          </div>
        </div>

        {/* Social Links */}
        <div className="flex items-center gap-3">
          <a
            href="https://github.com/simonyi29"
            target="_blank"
            rel="noopener noreferrer"
            className="w-11 h-11 rounded-lg flex items-center justify-center border border-cyber-border-subtle hover:border-cyber-neon-cyan hover:shadow-glow-cyan-sm transition-all bg-cyber-bg-tertiary hover:scale-110"
            title="GitHub"
          >
            <Github className="w-6 h-6" />
          </a>
          <a
            href="https://github.com/NanmiCoder/MediaCrawler"
            target="_blank"
            rel="noopener noreferrer"
            className="w-11 h-11 rounded-lg flex items-center justify-center border border-cyber-border-subtle hover:border-pink-400 hover:shadow-[0_0_10px_rgba(251,113,133,0.4)] transition-all bg-cyber-bg-tertiary hover:scale-110"
            title="MediaCrawler upstream"
          >
            <Github className="w-6 h-6" />
          </a>
        </div>
      </div>
    </footer>
  )
}
