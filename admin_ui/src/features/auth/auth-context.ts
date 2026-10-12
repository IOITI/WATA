import { createContext, useContext } from 'react'

import type { Session } from '@/lib/types'

export interface AuthState {
  /** `undefined` while the first check is in flight, `null` when signed out. */
  session: Session | null | undefined
  /** The server could not be asked (as opposed to answering "not signed in"). */
  error: string | null
  login: (password: string, code?: string) => Promise<void>
  logout: () => Promise<void>
}

export const AuthContext = createContext<AuthState | null>(null)

export function useAuth(): AuthState {
  const context = useContext(AuthContext)
  if (!context) throw new Error('useAuth must be used inside <AuthProvider>')
  return context
}

/** The session of a signed-in page. Only call below <RequireAuth>. */
export function useSession(): Session {
  const { session } = useAuth()
  if (!session) throw new Error('useSession must be used inside <RequireAuth>')
  return session
}
