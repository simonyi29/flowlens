import { Sun, Moon, Monitor } from 'lucide-react'
import { useThemeStore } from '@/store/themeStore'
import { useTranslation } from 'react-i18next'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'

type Theme = 'light' | 'dark' | 'system'

const themes: { value: Theme; labelKey: string; icon: typeof Sun }[] = [
  { value: 'light', labelKey: 'theme.light', icon: Sun },
  { value: 'dark', labelKey: 'theme.dark', icon: Moon },
  { value: 'system', labelKey: 'theme.system', icon: Monitor },
]

export function ThemeToggle() {
  const { theme, setTheme } = useThemeStore()
  const { t } = useTranslation('product')

  const currentTheme = themes.find(t => t.value === theme) || themes[0]
  const Icon = currentTheme.icon

  return (
    <Select value={theme} onValueChange={(value: Theme) => setTheme(value)}>
      <SelectTrigger aria-label={t('theme.label')} title={t('theme.label')} className="h-10 w-10 border-slate-200 bg-white text-xs transition-colors hover:border-teal-300 sm:h-9 sm:w-28">
        <Icon className="h-3.5 w-3.5 shrink-0 text-slate-500" />
        <SelectValue><span className="hidden truncate sm:inline">{t(currentTheme.labelKey)}</span></SelectValue>
      </SelectTrigger>
      <SelectContent>
        {themes.map(({ value, labelKey, icon: ItemIcon }) => (
          <SelectItem key={value} value={value} className="text-xs">
            <div className="flex items-center gap-2">
              <ItemIcon className="w-3 h-3" />
              {t(labelKey)}
            </div>
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}
