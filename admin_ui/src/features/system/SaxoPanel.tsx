import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, Copy, ExternalLink } from 'lucide-react'
import { useState } from 'react'
import { Link, useSearchParams } from 'react-router'

import { BrandMark } from '@/components/BrandMark'
import { Panel } from '@/components/Panel'
import { QueryState } from '@/components/status'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { FormControl, FormDescription, FormItem, FormLabel, Input } from '@/components/ui/form'
import { StepPanels, Stepper } from '@/components/ui/stepper'
import { useSession } from '@/features/auth/auth-context'
import { api } from '@/lib/api'
import { formatDateTime, formatRelative } from '@/lib/format'
import type { SaxoStatus } from '@/lib/types'

import { extractAuthorizationCode, extractAuthorizationState } from './authorizationCode'

const ACCOUNT: Record<string, string> = { live: 'live account', simulation: 'simulation account' }

function TokenFigures({ status, timeZone }: { status: SaxoStatus; timeZone: string }) {
  const figures: [string, string, string][] = [
    [
      'Saved',
      formatDateTime(status.saved_at, timeZone),
      status.saved_by
        ? `${formatRelative(status.saved_at)}, by the ${status.saved_by.replaceAll('_', ' ')}`
        : formatRelative(status.saved_at),
    ],
    [
      'Access token expires',
      formatDateTime(status.access_token_expires_at, timeZone),
      formatRelative(status.access_token_expires_at),
    ],
    [
      'Refresh token expires',
      formatDateTime(status.refresh_token_expires_at, timeZone),
      formatRelative(status.refresh_token_expires_at),
    ],
  ]
  return (
    <dl className="grid gap-x-6 gap-y-3 text-xs sm:grid-cols-3">
      {figures.map(([label, value, relative]) => (
        <div key={label}>
          <dt className="text-muted-foreground">{label}</dt>
          <dd className="mt-0.5 font-medium tabular-nums">{value}</dd>
          <dd className="text-muted-foreground">{relative}</dd>
        </div>
      ))}
    </dl>
  )
}

/** The address Saxo sends the browser back to, which the app must register. */
function RedirectAddress({ status }: { status: SaxoStatus }) {
  const [copied, setCopied] = useState(false)
  if (!status.redirect_url) {
    return (
      <p className="text-xs text-muted-foreground">
        No redirect address: set <code>SAXO_REDIRECT_URL</code> in <code>.env</code> (the admin&apos;s{' '}
        <code>/api/saxo/callback</code>), and register it in the app in Saxo&apos;s developer portal.
      </p>
    )
  }
  return (
    <div className="space-y-1 text-xs">
      <p className="text-muted-foreground">Redirect address, to register in the app in Saxo&apos;s developer portal:</p>
      <div className="flex flex-wrap items-center gap-2">
        <code className="break-all rounded-md border border-border bg-surface-raised/60 px-2 py-1">{status.redirect_url}</code>
        <Button
          variant="ghost"
          size="sm"
          leftIcon={copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
          onClick={() => void navigator.clipboard?.writeText(status.redirect_url!).then(() => setCopied(true))}
        >
          {copied ? 'Copied' : 'Copy'}
        </Button>
      </div>
    </div>
  )
}

function Authorisation({ status, actionsEnabled }: { status: SaxoStatus; actionsEnabled: boolean }) {
  const queryClient = useQueryClient()
  const [step, setStep] = useState(0)
  const [input, setInput] = useState('')
  const code = extractAuthorizationCode(input)
  const steps = [
    { title: 'Authorise', description: 'Sign in at Saxo' },
    status.callback
      ? { title: 'Back here', description: 'The code is taken' }
      : { title: 'Paste the address', description: 'From the address bar' },
    { title: 'Token', description: 'A service fetches it' },
  ]

  const authorize = useMutation({
    mutationFn: () => api.authorizeSaxo(status.environment),
    onSuccess: ({ url }) => {
      window.open(url, '_blank', 'noopener,noreferrer')
      setStep(1)
    },
  })
  const submit = useMutation({
    mutationFn: () => api.submitSaxoAuthCode(code, extractAuthorizationState(input)),
    onSuccess: () => {
      setInput('')
      setStep(2)
      void queryClient.invalidateQueries({ queryKey: ['saxo'] })
    },
  })

  return (
    <div className="space-y-5">
      <Stepper steps={steps} current={step} onStepChange={setStep} />
      <StepPanels current={step}>
        <div className="space-y-3 text-sm text-text-secondary">
          <p>
            Open Saxo&apos;s login page for the {ACCOUNT[status.environment ?? 'live']}, sign in and accept. The link is made
            for this login alone and lasts ten minutes.
          </p>
          <div className="flex flex-wrap gap-2">
            <Button
              variant="outline"
              size="sm"
              rightIcon={<ExternalLink className="size-3.5" aria-hidden />}
              disabled={!actionsEnabled}
              isLoading={authorize.isPending}
              onClick={() => authorize.mutate()}
            >
              Open Saxo&apos;s login
            </Button>
            <Button variant="ghost" size="sm" onClick={() => setStep(1)}>
              {status.callback ? 'I have signed in' : 'I have the address'}
            </Button>
          </div>
          {authorize.isError && (
            <Alert tone="danger" role="alert">
              <AlertDescription>{authorize.error.message}</AlertDescription>
            </Alert>
          )}
          <RedirectAddress status={status} />
        </div>

        <div className="max-w-xl space-y-3">
          {status.callback && (
            <p className="text-sm text-text-secondary">
              Saxo sends the browser back to this admin, which hands the code over by itself and comes back to this page.
              If it went to another address, paste it here.
            </p>
          )}
          <FormItem>
            <FormLabel htmlFor="saxo-code">Redirect address or code</FormLabel>
            <FormControl>
              <Input
                id="saxo-code"
                autoComplete="off"
                spellCheck={false}
                placeholder="https://…?code=…&state=… or the code itself"
                value={input}
                disabled={!actionsEnabled}
                onChange={(event) => setInput(event.target.value)}
              />
            </FormControl>
            <FormDescription>
              The whole address: its state shows it comes from the link made here. A code alone is taken too.
            </FormDescription>
          </FormItem>
          {submit.isError && (
            <Alert tone="danger" role="alert">
              <AlertDescription>{submit.error.message}</AlertDescription>
            </Alert>
          )}
          {!actionsEnabled && (
            <Alert tone="neutral">
              <AlertDescription>
                Operator actions are switched off in the configuration (<code>admin.actions_enabled</code>): use{' '}
                <code>watasaxoauth</code> on the server.
              </AlertDescription>
            </Alert>
          )}
          <Button size="sm" disabled={!code || !actionsEnabled} isLoading={submit.isPending} onClick={() => submit.mutate()}>
            Hand the code over
          </Button>
        </div>

        <Alert tone="info">
          <AlertTitle>Code handed over</AlertTitle>
          <AlertDescription>
            Sealed for the services that log in to Saxo: the first that needs a token exchanges it within seconds and
            says so on Telegram. This panel shows the token&apos;s dates as soon as it is saved.
          </AlertDescription>
        </Alert>
      </StepPanels>
    </div>
  )
}

/**
 * What the admin's callback came to, at the top of the page it leads back to
 * (the Saxo panel is below the services, which load after it).
 */
export function SaxoCallbackOutcome() {
  const [params, setParams] = useSearchParams()
  const outcome = params.get('saxo')
  if (!outcome) return null
  const dismiss = () => {
    const next = new URLSearchParams(params)
    next.delete('saxo')
    next.delete('why')
    setParams(next, { replace: true })
  }
  return outcome === 'handed-over' ? (
    <Alert tone="success" dismissible onDismiss={dismiss}>
      <AlertTitle>Saxo&apos;s login came back</AlertTitle>
      <AlertDescription>
        Its code was handed over: a service exchanges it within seconds, and Saxo connection below shows the new login.
      </AlertDescription>
    </Alert>
  ) : (
    <Alert tone="danger" role="alert" dismissible onDismiss={dismiss}>
      <AlertTitle>Saxo&apos;s login did not come back as expected</AlertTitle>
      <AlertDescription>{params.get('why') ?? 'No code'}. A new link is made under Saxo connection, below.</AlertDescription>
    </Alert>
  )
}

export function SaxoPanel() {
  const { timezone, actions_enabled: actionsEnabled } = useSession()
  const saxoQuery = useQuery({
    queryKey: ['saxo'],
    queryFn: api.saxo,
    // Often while an authorisation is needed, so the new token shows up by itself.
    refetchInterval: (query) =>
      query.state.data && (!query.state.data.has_token || query.state.data.refresh_token_expired) ? 3_000 : 30_000,
  })
  const status = saxoQuery.data
  const known = status?.where !== 'none'
  const needsAuthorisation = !!status && known && (!status.has_token || status.refresh_token_expired)
  const app = status?.environment ? status.apps?.[status.environment] : undefined

  return (
    <Panel
      title={
        <span className="inline-flex items-center gap-2">
          <BrandMark brand="saxo" />
          Saxo connection
        </span>
      }
      description={`The login the services share${status?.environment ? `, on the ${ACCOUNT[status.environment]}` : ''}. They renew it by themselves until its refresh token ends.`}
      action={
        status &&
        (!known ? (
          <Badge tone="warning" dot>
            Not in PostgreSQL yet
          </Badge>
        ) : needsAuthorisation ? (
          <Badge tone="danger" dot>
            Authorisation required
          </Badge>
        ) : (
          <Badge tone="success" dot>
            Authorised
          </Badge>
        ))
      }
    >
      <div className="space-y-4">
        <QueryState isPending={saxoQuery.isPending} error={saxoQuery.error} hasData={!!status}>
          {status && (
            <div className="space-y-4">
              {!known && (
                <Alert tone="warning">
                  <AlertDescription>
                    Saxo&apos;s login has no row in PostgreSQL yet. Either the services still keep it in their token file
                    of before, which this admin no longer reads (move it in with the migration, README › Credentials;
                    until then <code>watasaxoauth</code> on the server hands a login over), or none of the services that
                    log in to Saxo has started since the install.
                  </AlertDescription>
                </Alert>
              )}
              {app && !app.set && (
                <Alert tone="neutral">
                  <AlertDescription>
                    The {ACCOUNT[status.environment!]}&apos;s app is not sealed in PostgreSQL yet: the services read
                    config.json&apos;s. Set it in{' '}
                    <Link className="underline underline-offset-2" to="/config/credentials">
                      Configuration, Credentials
                    </Link>
                    .
                  </AlertDescription>
                </Alert>
              )}
              {status.last_refusal && needsAuthorisation && (
                <p className="text-xs text-muted-foreground">Saxo&apos;s last answer: {status.last_refusal}</p>
              )}
              {needsAuthorisation ? (
                <Authorisation status={status} actionsEnabled={actionsEnabled} />
              ) : (
                known && <TokenFigures status={status} timeZone={timezone} />
              )}
            </div>
          )}
        </QueryState>
      </div>
    </Panel>
  )
}
