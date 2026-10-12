import { useQuery } from '@tanstack/react-query'
import { Lock } from 'lucide-react'
import { useState } from 'react'
import type { FormEvent } from 'react'
import { Navigate, useLocation } from 'react-router'

import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Form, FormControl, FormItem, FormLabel, Input } from '@/components/ui/form'
import { api, ApiError } from '@/lib/api'
import { formatDuration } from '@/lib/format'

import { useAuth } from './auth-context'
import { returnPath } from './returnPath'

function loginErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 429 && error.retryAfter) {
      return `Too many failed attempts. Try again in ${formatDuration(error.retryAfter * 1000)}.`
    }
    return error.message
  }
  return 'Sign-in failed'
}

export function LoginPage() {
  const { session, login } = useAuth()
  const location = useLocation()
  const [password, setPassword] = useState('')
  const [code, setCode] = useState('')
  // Whether a one-time code is asked for as well: known before the first attempt.
  const methodsQuery = useQuery({ queryKey: ['login-methods'], queryFn: api.loginMethods, staleTime: 60_000 })
  const needsCode = methodsQuery.data?.totp ?? false
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  if (session) {
    return <Navigate to={returnPath((location.state as { from?: unknown } | null)?.from)} replace />
  }

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!password || submitting || (needsCode && !code)) return
    setSubmitting(true)
    setError(null)
    try {
      await login(password, needsCode ? code : undefined)
    } catch (loginError) {
      setError(loginErrorMessage(loginError))
      setPassword('')
      setCode('')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <main className="grid min-h-dvh place-items-center p-6">
      <div className="w-full max-w-sm rounded-2xl border border-border/70 bg-surface/50 p-8 shadow-elevated dark:bg-surface/25">
        <div className="mb-6 flex flex-col gap-1.5">
          <div className="mb-3 grid size-10 place-items-center rounded-xl border border-border bg-surface-raised">
            <Lock className="size-4 text-foreground" aria-hidden />
          </div>
          <h1 className="text-xl font-semibold tracking-tight">WATA Admin</h1>
          <p className="text-sm text-muted-foreground">
            {needsCode ? 'Sign in with the operator password and a one-time code.' : 'Sign in with the operator password.'}
          </p>
        </div>

        <Form onSubmit={handleSubmit}>
          {error && (
            <Alert tone="danger" role="alert">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}
          <FormItem>
            <FormLabel htmlFor="password">Password</FormLabel>
            <FormControl>
              <Input
                id="password"
                name="password"
                type="password"
                autoComplete="current-password"
                autoFocus
                showPasswordToggle
                value={password}
                onChange={(event) => setPassword(event.target.value)}
              />
            </FormControl>
          </FormItem>
          {needsCode && (
            <FormItem>
              <FormLabel htmlFor="code">One-time code</FormLabel>
              <FormControl>
                <Input
                  id="code"
                  name="code"
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  placeholder="6 digits from the authenticator app"
                  maxLength={7}
                  value={code}
                  onChange={(event) => setCode(event.target.value.replace(/[^\d ]/g, ''))}
                />
              </FormControl>
            </FormItem>
          )}
          <Button
            type="submit"
            fullWidth
            isLoading={submitting}
            loadingText="Signing in"
            disabled={!password || (needsCode && !code)}
          >
            Sign in
          </Button>
        </Form>
      </div>
    </main>
  )
}
