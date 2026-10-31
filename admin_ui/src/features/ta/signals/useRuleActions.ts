// A rule's mode and its deletion, as the Signal rules page and a rule's own page ask them: off and shadow are switched
// at once, live and leaving it through RuleModeDialog's confirmation (`changing`), a deletion through DeleteRuleDialog
// (`deleting`). Each names the rules' revision the page read: refused (409) once the rules changed meanwhile.

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useToast } from 'ioiti-ui'
import { useState } from 'react'

import { api } from '@/lib/api'
import type { TaRule, TaRuleMode } from '@/lib/types'

import { MODE_LABEL } from './ruleModel'

export interface ModeChange {
  rule: TaRule
  mode: TaRuleMode
  /** Live although the server's readiness checks are not all met: recorded so in the audit trail. */
  force?: boolean
}

export function useRuleActions(revision: string | undefined, { onDeleted }: { onDeleted?: (rule: TaRule) => void } = {}) {
  const toast = useToast()
  const queryClient = useQueryClient()
  const [changing, setChanging] = useState<ModeChange | null>(null)
  const [deleting, setDeleting] = useState<TaRule | null>(null)

  const refresh = () => Promise.all([['ta-rules'], ['ta-engine']].map((queryKey) => queryClient.invalidateQueries({ queryKey })))

  const modeMutation = useMutation({
    mutationFn: ({ rule, mode, force }: ModeChange) => api.setTaRuleMode(rule.id, mode, revision!, force),
    onSuccess: async (saved, { force }) => {
      setChanging(null)
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
    onError: () => void refresh(),
  })
  const deleteMutation = useMutation({
    mutationFn: (rule: TaRule) => api.deleteTaRule(rule.id, revision!),
    onSuccess: async (_, rule) => {
      setDeleting(null)
      toast({ title: 'Signal rule deleted', variant: 'success' })
      onDeleted?.(rule)
      await refresh()
    },
    onError: () => void refresh(),
  })

  const askMode = (rule: TaRule, mode: TaRuleMode) => {
    if (mode === rule.mode) return
    // Off and shadow change nothing that trades: at once. Live, and leaving it, are said first.
    if (mode !== 'live' && rule.mode !== 'live') modeMutation.mutate({ rule, mode })
    else {
      modeMutation.reset()
      setChanging({ rule, mode })
    }
  }
  const askDelete = (rule: TaRule) => {
    deleteMutation.reset()
    setDeleting(rule)
  }

  return { changing, setChanging, deleting, setDeleting, askMode, askDelete, modeMutation, deleteMutation, refresh }
}
