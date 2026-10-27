import { Alert, AlertDescription, AlertTitle, Loader } from 'ioiti-ui'
import type { ReactNode } from 'react'
import { Navigate, useLocation } from 'react-router'

import { useAuth } from './auth-context'

export function RequireAuth({ children }: { children: ReactNode }) {
  const { session, error } = useAuth()
  const location = useLocation()

  if (error) {
    return (
      <div className="grid min-h-dvh place-items-center p-6">
        <Alert tone="danger" className="max-w-md">
          <AlertTitle>The admin server cannot be reached</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      </div>
    )
  }

  if (session === undefined) {
    return (
      <div className="grid min-h-dvh place-items-center">
        <Loader label="Loading" />
      </div>
    )
  }

  if (session === null) {
    return <Navigate to="/login" replace state={{ from: `${location.pathname}${location.search}` }} />
  }

  return children
}
