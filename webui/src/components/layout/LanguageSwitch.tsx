import { Globe } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'

const languages = [
  { code: 'zh-CN', label: '中文' },
  { code: 'en-US', label: 'EN' },
]

export function LanguageSwitch() {
  const { i18n, t } = useTranslation('product')

  const currentLang = languages.find(l => l.code === i18n.language) || languages[0]

  return (
    <Select value={i18n.resolvedLanguage || i18n.language} onValueChange={(lang) => i18n.changeLanguage(lang)}>
      <SelectTrigger aria-label={t('language.label')} title={t('language.label')} className="h-10 w-10 border-slate-200 bg-white text-xs transition-colors hover:border-teal-300 sm:h-9 sm:w-20">
        <Globe className="h-3.5 w-3.5 shrink-0 text-slate-500" />
        <SelectValue><span className="hidden sm:inline">{t(`language.${currentLang.code}`)}</span></SelectValue>
      </SelectTrigger>
      <SelectContent>
        {languages.map((lang) => (
          <SelectItem key={lang.code} value={lang.code} className="text-xs">
            {t(`language.${lang.code}`)}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}
