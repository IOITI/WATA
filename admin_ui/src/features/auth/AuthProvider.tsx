import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useMemo } from 'react'
import type { ReactNode } from 'react'

import { api, ApiError, setUnauthorizedListener } from '@/lib/api'
import type { Session } from '@/lib/types'

import { AuthContext } from './auth-context'
import type { AuthState } from './auth-context'

const SESSION_KEY = ['session'] as const

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient()

  const sessionQuery = useQuery({
    queryKey: SESSION_KEY,
    queryFn: async (): Promise<Session | null> => {
      try {
        return await api.session()
      } catch (error) {
        if (error instanceof ApiError && error.status === 401) return null
        throw error
      }
    },
    // Also picks up "restart required" after a config save, and a session that expired.
    refetchInterval: 60_000,
    staleTime: 10_000,
  })

  const signOutLocally = useCallback(() => {
    queryClient.setQueryData(SESSION_KEY, null)
    // Nothing fetched under the old session may be shown to whoever signs in next.
    queryClient.removeQueries({ predicate: (query) => query.queryKey[0] !== SESSION_KEY[0] })
  }, [queryClient])

  useEffect(() => {
    setUnauthorizedListener(signOutLocally)
    return () => setUnauthorizedListener(null)
  }, [signOutLocally])

  const login = useCallback(
    async (password: string, code?: string) => {
      await api.login(password, code)
      await queryClient.invalidateQueries({ queryKey: SESSION_KEY })
    },
    [queryClient],
  )

  const logout = useCallback(async () => {
    try {
      await api.logout()
    } finally {
      signOutLocally()
    }
  }, [signOutLocally])

  const value = useMemo<AuthState>(
    () => ({
      session: sessionQuery.isPending ? undefined : (sessionQuery.data ?? null),
      error: sessionQuery.isError ? sessionQuery.error.message : null,
      login,
      logout,
    }),
    [sessionQuery.isPending, sessionQuery.data, sessionQuery.isError, sessionQuery.error, login, logout],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
