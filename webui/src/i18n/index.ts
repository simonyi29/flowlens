import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'
import LanguageDetector from 'i18next-browser-languagedetector'

// 中文翻译
import zhCommon from './locales/zh-CN/common.json'
import zhConfig from './locales/zh-CN/config.json'
import zhTerminal from './locales/zh-CN/terminal.json'
import zhData from './locales/zh-CN/data.json'
import zhEnv from './locales/zh-CN/env.json'
import zhLicense from './locales/zh-CN/license.json'
import zhProduct from './locales/zh-CN/product.json'

// 英文翻译
import enCommon from './locales/en-US/common.json'
import enConfig from './locales/en-US/config.json'
import enTerminal from './locales/en-US/terminal.json'
import enData from './locales/en-US/data.json'
import enEnv from './locales/en-US/env.json'
import enLicense from './locales/en-US/license.json'
import enProduct from './locales/en-US/product.json'

const resources = {
  'zh-CN': {
    common: zhCommon,
    config: zhConfig,
    terminal: zhTerminal,
    data: zhData,
    env: zhEnv,
    license: zhLicense,
    product: zhProduct,
  },
  'en-US': {
    common: enCommon,
    config: enConfig,
    terminal: enTerminal,
    data: enData,
    env: enEnv,
    license: enLicense,
    product: enProduct,
  },
}

i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources,
    fallbackLng: 'zh-CN',
    defaultNS: 'common',
    interpolation: {
      escapeValue: false,
    },
    detection: {
      order: ['localStorage', 'navigator'],
      caches: ['localStorage'],
      lookupLocalStorage: 'flowlens_language',
    },
  })

if (typeof document !== 'undefined') {
  document.documentElement.lang = i18n.resolvedLanguage || i18n.language || 'zh-CN'
  i18n.on('languageChanged', (language) => {
    document.documentElement.lang = language
  })
}

export default i18n
