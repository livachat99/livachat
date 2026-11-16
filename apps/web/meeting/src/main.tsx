import '@livekit/components-styles'
import '@livekit/components-styles/prefabs'
import { createRoot } from 'react-dom/client'
import App from './App'
import { installDeviceLabelFallback } from './deviceLabels'
import {
  applyDocumentLocale,
  browserLanguages,
  createI18n,
  I18nProvider,
  localeFromQuery,
  resolveLocale,
} from './i18n'
import './styles/globals.css'

const requestedLocale = localeFromQuery(window.location.search)
const locale = requestedLocale
  ? resolveLocale([requestedLocale])
  : resolveLocale(browserLanguages())
applyDocumentLocale(locale)
const i18n = createI18n(locale)
installDeviceLabelFallback(i18n.messages)

createRoot(document.getElementById('root')!).render(
  <I18nProvider value={i18n}>
    <App />
  </I18nProvider>,
)
