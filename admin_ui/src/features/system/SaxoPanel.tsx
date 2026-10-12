import { useMutation, useQuery } from '@tanstack/react-query'
import { ExternalLink } from 'lucide-react'
import { useState } from 'react'

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

import { extractAuthorizationCode } from './authorizationCode'

const STEPS = [
  { title: 'Authorise', description: 'Sign in at Saxo' },
  { title: 'Paste the code', description: 'From the address bar' },
  { title: 'Token', description: 'The service fetches it' },
]

function TokenFigures({ status, timeZone }: { status: SaxoStatus; timeZone: string }) {
  const figures: [string, string, string][] = [
    ['Saved', formatDateTime(status.saved_at, timeZone), formatRelative(status.saved_at)],
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

function Authorisation({ status, actionsEnabled }: { status: SaxoStatus; actionsEnabled: boolean }) {
  const [step, setStep] = useState(0)
  const [input, setInput] = useState('')
  const code = extractAuthorizationCode(input)

  const submitMutation = useMutation({
    mutationFn: () => api.submitSaxoAuthCode(code),
    onSuccess: () => {
      setInput('')
      setStep(2)
    },
  })

  return (
    <div className="space-y-5">
      <Stepper steps={STEPS} current={step} onStepChange={setStep} />
      <StepPanels current={step}>
        <div className="space-y-3 text-sm text-text-secondary">
          <p>
            Open Saxo&apos;s authorisation page, sign in and accept. Saxo then sends the browser to the application&apos;s
            redirect address, which usually shows an error page: that is expected, the code is in its address.
          </p>
          <div className="flex flex-wrap gap-2">
            <a
              href={status.authorization_url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex h-8 items-center gap-1.5 rounded-md border border-border px-3 text-xs font-medium text-foreground hover:bg-surface-hover"
            >
              Open the Saxo authorisation page
              <ExternalLink className="size-3.5" aria-hidden />
            </a>
            <Button variant="ghost" size="sm" onClick={() => setStep(1)}>
              I have the address
            </Button>
          </div>
        </div>

        <div className="max-w-xl space-y-3">
          <FormItem>
            <FormLabel htmlFor="saxo-code">Redirect address or code</FormLabel>
            <FormControl>
              <Input
                id="saxo-code"
                autoComplete="off"
                spellCheck={false}
                placeholder="https://…?code=… or the code itself"
                value={input}
                disabled={!actionsEnabled}
                onChange={(event) => setInput(event.target.value)}
              />
            </FormControl>
            <FormDescription>
              Paste the whole address of the page Saxo redirected to; the code is taken from it. It is handed to the
              service that is waiting, which waits five minutes after asking.
            </FormDescription>
          </FormItem>
          {submitMutation.isError && (
            <Alert tone="danger" role="alert">
              <AlertDescription>{submitMutation.error.message}</AlertDescription>
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
          <Button
            size="sm"
            disabled={!code || !actionsEnabled}
            isLoading={submitMutation.isPending}
            onClick={() => submitMutation.mutate()}
          >
            Hand the code over
          </Button>
        </div>

        <Alert tone="info">
          <AlertTitle>Code handed over</AlertTitle>
          <AlertDescription>
            The waiting service exchanges it for a token within a few seconds and confirms on Telegram. This panel
            switches to the token&apos;s dates as soon as it is saved. If nothing changes, the service was no longer
            waiting: hand over a new code when it asks again.
          </AlertDescription>
        </Alert>
      </StepPanels>
    </div>
  )
}

export function SaxoPanel() {
  const { timezone, actions_enabled: actionsEnabled } = useSession()
  const saxoQuery = useQuery({
    queryKey: ['saxo'],
    queryFn: api.saxo,
    // Often while an authorisation is needed, so the new token shows up by itself.
    refetchInterval: (query) => (query.state.data?.refresh_token_expired ? 3_000 : 30_000),
  })
  const status = saxoQuery.data
  const needsAuthorisation = !!status && (!status.has_token || status.refresh_token_expired)

  return (
    <Panel
      title="Saxo connection"
      description="The token shared by the services. They renew it by themselves until the refresh token expires."
      action={
        status &&
        (needsAuthorisation ? (
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
      <QueryState isPending={saxoQuery.isPending} error={saxoQuery.error} hasData={!!status}>
        {status &&
          (needsAuthorisation ? (
            <Authorisation status={status} actionsEnabled={actionsEnabled} />
          ) : (
            <TokenFigures status={status} timeZone={timezone} />
          ))}
      </QueryState>
    </Panel>
  )
}
