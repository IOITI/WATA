import { useCallback, useLayoutEffect, useMemo, useState, useSyncExternalStore } from 'react'
import type { ReactNode } from 'react'

import { applyTheme, readPreference, resolveTheme, systemIsDark, watchSystem, writePreference } from '@/lib/theme'
import type { ThemePreference } from '@/lib/theme'

import { ThemeContext } from './theme-context'

/**
 * Holds the theme for the whole interface, the login page included. While the
 * preference is to follow the system, a change of the system setting (a phone
 * going dark in the evening) is followed at once.
 */
export function ThemeProvider({ children }: { children: ReactNode }) {
  const [preference, setStored] = useState<ThemePreference>(readPreference)
  const systemDark = useSyncExternalStore(watchSystem, systemIsDark)
  const theme = resolveTheme(preference, systemDark)

  // Before the browser paints: no frame in the theme being left.
  useLayoutEffect(() => {
    applyTheme(theme)
  }, [theme])

  const setPreference = useCallback((next: ThemePreference) => {
    writePreference(next)
    setStored(next)
  }, [])

  const value = useMemo(() => ({ preference, theme, setPreference }), [preference, theme, setPreference])
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
}
