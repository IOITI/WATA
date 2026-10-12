import { createContext, useContext } from 'react'

import type { Theme, ThemePreference } from '@/lib/theme'

export interface ThemeState {
  /** What was chosen: a theme, or to follow the system. */
  preference: ThemePreference
  /** What is shown. */
  theme: Theme
  setPreference: (preference: ThemePreference) => void
}

// Outside a provider (a component rendered alone in a test), the interface is as it always was: dark.
export const ThemeContext = createContext<ThemeState>({ preference: 'dark', theme: 'dark', setPreference: () => {} })

export function useTheme(): ThemeState {
  return useContext(ThemeContext)
}
