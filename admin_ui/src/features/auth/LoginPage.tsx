import { useQuery } from '@tanstack/react-query'
import { Alert, AlertDescription, Button, cn, Form, formatDuration, FormControl, FormItem, FormLabel, Input, OTPInput, ThemeToggle } from 'ioiti-ui'
import { Lock } from 'lucide-react'
import { useState } from 'react'
import type { FormEvent } from 'react'
import { Navigate, useLocation } from 'react-router'

import { api, ApiError } from '@/lib/api'

import { useAuth } from './auth-context'
import { returnPath } from './returnPath'

/** The digits of the authenticator app's code (TOTP_DIGITS on the server). */
const CODE_LENGTH = 6

// Six boxes that look like the password field above them, and fill its width.
const CODE_BOX = cn(
  'h-11 w-full rounded-lg border text-base transition-colors duration-150',
  'border-neutral-300 bg-white text-neutral-900 hover:border-neutral-500 focus:border-neutral-500',
  'dark:border-[#1F1F1F] dark:bg-[#141414] dark:text-[#FAFAFA] dark:hover:border-[#4A4A4A] dark:focus:border-[#4A4A4A]',
)

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
  // The boxes keep their digits where they were typed (a value given to them would be laid out from the
  // first box): they are made again, empty, after a refused attempt.
  const [codeBoxes, setCodeBoxes] = useState(0)
  // Whether a one-time code is asked for as well: known before the first attempt.
  const methodsQuery = useQuery({ queryKey: ['login-methods'], queryFn: api.loginMethods, staleTime: 60_000 })
  const needsCode = methodsQuery.data?.totp ?? false
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  if (session) {
    return <Navigate to={returnPath((location.state as { from?: unknown } | null)?.from)} replace />
  }

  const signIn = async (typedCode: string) => {
    if (!password || submitting || (needsCode && typedCode.length < CODE_LENGTH)) return
    setSubmitting(true)
    setError(null)
    try {
      await login(password, needsCode ? typedCode : undefined)
    } catch (loginError) {
      setError(loginErrorMessage(loginError))
      setPassword('')
      setCode('')
      setCodeBoxes((made) => made + 1)
    } finally {
      setSubmitting(false)
    }
  }

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    void signIn(code)
  }

  return (
    <main className="relative grid min-h-dvh place-items-center p-6">
      {/* The theme can be chosen before signing in: this page is the first one seen. */}
      <ThemeToggle className="absolute right-4 top-4" />
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
                <OTPInput
                  key={codeBoxes}
                  id="code"
                  aria-label="One-time code"
                  length={CODE_LENGTH}
                  onChange={setCode}
                  // The last digit signs in, once the password is there; Enter does too.
                  onComplete={(completed) => void signIn(completed)}
                  disabled={submitting}
                  className="grid grid-cols-6 gap-2"
                  boxClassName={CODE_BOX}
                />
              </FormControl>
            </FormItem>
          )}
          <Button
            type="submit"
            fullWidth
            isLoading={submitting}
            loadingText="Signing in"
            disabled={!password || (needsCode && code.length < CODE_LENGTH)}
          >
            Sign in
          </Button>
        </Form>
      </div>
    </main>
  )
}
