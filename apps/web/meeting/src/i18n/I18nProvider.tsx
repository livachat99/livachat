import * as React from 'react'
import type { I18n } from './types'
import type { Messages } from './locales/en'

const I18nContext = React.createContext<I18n | null>(null)

export function I18nProvider({
  value,
  children,
}: React.PropsWithChildren<{ value: I18n }>) {
  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>
}

export function useI18n(): I18n {
  const value = React.useContext(I18nContext)
  if (!value) throw new Error('useI18n must be used inside I18nProvider')
  return value
}

export function useMessages(): Messages {
  return useI18n().messages
}
