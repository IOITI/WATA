// Switching a signal rule to live, or taking it out of live: the confirmation of the Signal rules page, and the AI
// agent's proposal of the same (its card), sent with the proposal it applies so that the audit trail says so. Going
// live says what the rule's alerts become, the web server's one signal per indice, and its readiness: short of it,
// only "Go live anyway" goes past, which the audit trail records with what was missing.

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Checkbox, CheckList, useToast } from 'ioiti-ui'
import type { Check } from 'ioiti-ui'
import { useState } from 'react'
import type { ReactNode } from 'react'

import { ConfirmLiveAction } from '@/components/ConfirmLiveAction'
import { api } from '@/lib/api'
import type { TaRule, TaRuleMode } from '@/lib/types'

import { holdingBack } from './readiness'
import { barsLabel, conflictText, MODE_LABEL, mappedAlerts, notReadyRefusal } from './ruleModel'

export function RuleModeDialog({ change, revision, readiness, onClose, proposal, onDone, onRefused, verdict }: {
  /** The rule and the mode it goes to; none: the dialog is closed. */
  change: { rule: TaRule; mode: TaRuleMode } | null
  /** The rules' revision the page read. */
  revision: string | undefined
  /** What the page knows of the rule's readiness (liveReadiness), going live only. */
  readiness: Check[]
  onClose: () => void
  /** The AI agent's proposal this applies (<conversation>/<proposal>). */
  proposal?: string
  onDone?: () => void
  /** What the server answered when it refused it. */
  onRefused?: (message: string) => void
  /** The AI agent's verdict on the rule (its go-live dossier), shown going live. */
  verdict?: ReactNode
}) {
  const toast = useToast()
  const queryClient = useQueryClient()
  // "Go live anyway", ticked in the confirmation of a rule not ready.
  const [anyway, setAnyway] = useState(false)
  const refresh = () => Promise.all([['ta-rules'], ['ta-engine']].map((queryKey) => queryClient.invalidateQueries({ queryKey })))
  const mutation = useMutation({
    mutationFn: ({ force }: { force: boolean }) => api.setTaRuleMode(change!.rule.id, change!.mode, revision!, force, proposal),
    onSuccess: async (saved, { force }) => {
      close()
      onDone?.()
      toast({
        title: `${saved.rule.name}: ${MODE_LABEL[saved.rule.mode].toLowerCase()}`,
        description:
          saved.rule.mode === 'live'
            ? `Its signals go to the trader from its next bar.${force ? ' Gone live without its readiness, as the audit trail records.' : ''}`
            : saved.rule.mode === 'shadow'
              ? 'Its signals are journaled, never sent.'
              : 'The engine stops running it.',
        variant: 'success',
      })
      await refresh()
    },
    onError: (error) => {
      void refresh()
      onRefused?.(conflictText(error) ?? error.message)
    },
  })
  const close = () => {
    setAnyway(false)
    mutation.reset()
    onClose()
  }
  const goingLive = change?.mode === 'live'
  const notReady = goingLive && (holdingBack(readiness).length > 0 || notReadyRefusal(mutation.error))

  return (
    <ConfirmLiveAction
      open={change !== null}
      title={goingLive ? `Switch ${change?.rule.name} to live` : `Take ${change?.rule.name} off live`}
      gesture={goingLive ? 'slide' : 'button'}
      confirmLabel={goingLive ? 'Slide to go live' : `Switch to ${change ? MODE_LABEL[change.mode].toLowerCase() : ''}`}
      doneLabel={goingLive ? 'Live' : 'Done'}
      pending={mutation.isPending}
      blocked={notReady && !anyway}
      error={conflictText(mutation.error)}
      warning={
        goingLive
          ? `The web server keeps one signal per indice, whoever sends it: switch TradingView's alerts for ${change?.rule.indice} off first, or both will trade it.`
          : undefined
      }
      description={
        change &&
        (goingLive ? (
          <div className="space-y-2">
            <p>
              From its next bar, the alerts of <strong>{change.rule.name}</strong> ({change.rule.asset.key},{' '}
              {barsLabel(change.rule.bars)}) become signals the trader acts on, for <strong>{change.rule.indice}</strong>:
            </p>
            <ul className="list-disc pl-5">
              {mappedAlerts(change.rule).map((entry) => (
                <li key={entry}>{entry}</li>
              ))}
            </ul>
            <p>The trader applies its usual rules to each of them. The switch is recorded in the audit trail.</p>
            <div className="space-y-2 pt-2">
              <p className="font-medium">Is it ready?</p>
              <CheckList checks={readiness} label="Is it ready?" />
              {verdict}
              {notReady ? (
                <>
                  <p className="text-xs text-muted-foreground">
                    Not ready: the server checks the same, all but the script’s version, and refuses the switch unless you
                    go live anyway. The audit trail then records what was missing.
                  </p>
                  <Checkbox label="Go live anyway" checked={anyway} onChange={(event) => setAnyway(event.target.checked)} />
                </>
              ) : (
                <p className="text-xs text-muted-foreground">What this page knows of it; the server checks the same before the switch.</p>
              )}
            </div>
          </div>
        ) : (
          <p>
            Its signals stop going to the trader from now on{change.mode === 'shadow' ? ': they are journaled only' : ''}.
          </p>
        ))
      }
      onConfirm={() => change && mutation.mutate({ force: notReady && anyway })}
      onClose={close}
    />
  )
}
