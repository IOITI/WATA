import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Send } from 'lucide-react'
import { useState } from 'react'

import { ConfirmLiveAction } from '@/components/ConfirmLiveAction'
import { PageHeader, Panel } from '@/components/Panel'
import { ActionBadge, QueryState } from '@/components/status'
import { useToast } from '@/components/toast-context'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { FormControl, FormDescription, FormItem, FormLabel, Input } from '@/components/ui/form'
import { SpringSelect } from '@/components/ui/spring-select'
import { useSession } from '@/features/auth/auth-context'
import { useFollow } from '@/features/live/follow-context'
import { api } from '@/lib/api'
import { formatDateTime } from '@/lib/format'
import { actionLabel } from '@/lib/labels'
import type { JsonObject } from '@/lib/types'

import { JournalPanel } from './JournalPanel'

const ACTION_OPTIONS = [
  { value: 'close-position', label: 'Close all positions', description: 'What the scheduler sends at the end of the day' },
  { value: 'close-long', label: 'Close long positions' },
  { value: 'close-short', label: 'Close short positions' },
  { value: 'long', label: 'Open long', description: 'Buys a turbo, like a TradingView alert' },
  { value: 'short', label: 'Open short', description: 'Buys a turbo, like a TradingView alert' },
]

const OPEN_ACTIONS = new Set(['long', 'short'])

/** The indices the trader accepts, from the allowed_indices rule of the configuration. */
function allowedIndices(config: JsonObject | undefined): string[] {
  const trade = config?.trade
  if (!trade || typeof trade !== 'object' || Array.isArray(trade)) return []
  const rules = trade.rules
  if (!Array.isArray(rules)) return []
  for (const rule of rules) {
    if (!rule || typeof rule !== 'object' || Array.isArray(rule) || rule.rule_type !== 'allowed_indices') continue
    const ruleConfig = rule.rule_config
    if (!ruleConfig || typeof ruleConfig !== 'object' || Array.isArray(ruleConfig)) continue
    const indices = ruleConfig.indice_ids
    if (indices && typeof indices === 'object' && !Array.isArray(indices)) return Object.keys(indices)
  }
  return []
}

function SendSignal() {
  const toast = useToast()
  const follow = useFollow()
  const queryClient = useQueryClient()
  const configQuery = useQuery({ queryKey: ['config'], queryFn: api.config, staleTime: 60_000 })
  const indices = allowedIndices(configQuery.data?.config)

  const [action, setAction] = useState('close-position')
  const [indice, setIndice] = useState('')
  const [confidence, setConfidence] = useState('')
  const [confirming, setConfirming] = useState(false)

  const opens = OPEN_ACTIONS.has(action)
  const selectedIndice = indice || indices[0] || ''
  const confidenceValue = confidence.trim() === '' ? undefined : Number(confidence)
  const confidenceInvalid =
    confidenceValue !== undefined && !(Number.isFinite(confidenceValue) && confidenceValue >= 0 && confidenceValue <= 2)
  const ready = opens ? selectedIndice !== '' && !confidenceInvalid : true

  const sendMutation = useMutation({
    // When it was sent, by this browser's clock: the card that follows it waits a minute for the trader's decision.
    mutationFn: (_sentAt: number) =>
      api.sendSignal(opens ? { action, indice: selectedIndice, confidence: confidenceValue } : { action }),
    onSuccess: (sent, sentAt) => {
      setConfirming(false)
      if (follow) {
        follow({
          kind: 'signal',
          signalUuid: sent.signal_uuid,
          action: sent.signal.action,
          indice: opens ? selectedIndice : null,
          sentAt,
        })
      } else {
        toast({
          title: `${actionLabel(sent.signal.action)} signal sent`,
          description: `The trader takes it on its next poll. Reference ${sent.signal_uuid.slice(0, 8)}.`,
          variant: 'success',
        })
      }
      void queryClient.invalidateQueries({ queryKey: ['signals'] })
    },
  })

  return (
    <div className="grid max-w-xl gap-4">
      <SpringSelect label="Signal" options={ACTION_OPTIONS} value={action} onChange={setAction} />

      {opens && (
        <>
          {indices.length === 0 ? (
            <Alert tone="warning">
              <AlertDescription>
                No indice is allowed in the configuration (rule <code>allowed_indices</code>), so nothing can be opened.
              </AlertDescription>
            </Alert>
          ) : (
            <SpringSelect
              label="Indice"
              options={indices.map((name) => ({ value: name, label: name }))}
              value={selectedIndice}
              onChange={setIndice}
            />
          )}
          <FormItem>
            <FormLabel htmlFor="confidence">Confidence (optional)</FormLabel>
            <FormControl>
              <Input
                id="confidence"
                inputMode="decimal"
                placeholder="0 to 2, e.g. 1"
                value={confidence}
                error={confidenceInvalid}
                onChange={(event) => setConfidence(event.target.value)}
              />
            </FormControl>
            <FormDescription>Scales the position size when confidence scaling is enabled.</FormDescription>
          </FormItem>
        </>
      )}

      <div>
        <Button
          leftIcon={<Send className="size-4" />}
          disabled={!ready}
          onClick={() => {
            sendMutation.reset()
            setConfirming(true)
          }}
        >
          Review and send
        </Button>
      </div>

      <ConfirmLiveAction
        open={confirming}
        title={`Send “${actionLabel(action)}”`}
        confirmLabel="Slide to send"
        doneLabel="Sent"
        pending={sendMutation.isPending}
        error={sendMutation.error?.message}
        onConfirm={() => sendMutation.mutate(Date.now())}
        onClose={() => setConfirming(false)}
        description={
          opens ? (
            <>
              The trader will try to open a <strong>{action}</strong> position on <strong>{selectedIndice}</strong>
              {confidenceValue !== undefined && <> with confidence {confidenceValue}</>}, exactly as for a TradingView
              alert. <strong>It also closes every position it already has open</strong>, whatever the indice.
              Depending on the sizing mode that happens before the new order is placed, so they can be closed even if
              that order then fails. Its rules apply first (market hours, daily limits, cooldown): if one refuses the
              signal, nothing is opened or closed. Check the trader log and the positions for the outcome.
            </>
          ) : (
            <>
              The trader will close{' '}
              <strong>
                {action === 'close-position' ? 'every open position' : `every open ${action.replace('close-', '')} position`}
              </strong>{' '}
              it manages, whatever the indice.
            </>
          )
        }
      />
    </div>
  )
}

export function SignalsPage() {
  const { timezone, actions_enabled: actionsEnabled } = useSession()
  const signalsQuery = useQuery({ queryKey: ['signals'], queryFn: api.signals, refetchInterval: 5_000 })
  const signals = signalsQuery.data?.items

  return (
    <>
      <PageHeader
        title="Signals"
        description="The web server keeps the latest signal per indice; the trader executes each one once."
      />

      <Panel title="Latest signal per indice" description={`Times are in ${timezone}.`} bodyClassName="pt-2">
        <QueryState isPending={signalsQuery.isPending} error={signalsQuery.error} hasData={!!signals}>
          {signals && signals.length === 0 && (
            <p className="py-6 text-center text-sm text-muted-foreground">No signal since the web server started.</p>
          )}
          {signals && signals.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[36rem] text-left text-xs">
                <thead className="text-muted-foreground">
                  <tr>
                    <th className="py-2 pr-4 font-medium">Signal</th>
                    <th className="py-2 pr-4 font-medium">Indice</th>
                    <th className="py-2 pr-4 font-medium">Signal time</th>
                    <th className="py-2 pr-4 font-medium">Received</th>
                    <th className="py-2 pr-4 text-right font-medium">Confidence</th>
                    <th className="py-2 font-medium">Reference</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border/40">
                  {signals.map((signal) => (
                    <tr key={signal.signal_uuid}>
                      <td className="py-2.5 pr-4">
                        <ActionBadge action={signal.action} />
                      </td>
                      <td className="py-2.5 pr-4 font-medium">{signal.indice}</td>
                      <td className="py-2.5 pr-4">{formatDateTime(signal.signal_timestamp, timezone)}</td>
                      <td className="py-2.5 pr-4">{formatDateTime(signal.received_timestamp, timezone)}</td>
                      <td className="py-2.5 pr-4 text-right tabular-nums">{signal.confidence ?? '—'}</td>
                      <td className="py-2.5 font-mono text-muted-foreground">{signal.signal_uuid.slice(0, 8)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </QueryState>
      </Panel>

      <Panel
        title="Send a signal"
        description="Goes through the web server like a webhook, so the trader handles it as it would any other signal."
      >
        {actionsEnabled ? (
          <SendSignal />
        ) : (
          <Alert tone="neutral">
            <AlertDescription>
              Operator actions are switched off in the configuration (<code>admin.actions_enabled</code>).
            </AlertDescription>
          </Alert>
        )}
      </Panel>

      <JournalPanel timeZone={timezone} />
      <p className="-mt-3 text-xs text-muted-foreground">
        Written by the trader as it handles each signal. A signal replaced by a newer one for the same indice before
        the trader read it never reaches the trader, and is not listed.
      </p>
    </>
  )
}
