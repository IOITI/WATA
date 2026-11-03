// The rule checks as the pages read them: what each script's source says is made by the
// server when first asked, a run test waits for the Pine studies. While one is waiting or
// under way, or reports are being made, they are read again every few seconds.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useToast } from 'ioiti-ui'

import { api } from '@/lib/api'
import type { RuleChecksState, RuleTestAsked } from '@/lib/types'

const AGAIN_MS = 5000

function busy(state: RuleChecksState | undefined): boolean {
  return Boolean(state && (state.queued.length > 0 || state.testing !== null || state.checking > 0))
}

/** Every script's newest version, and every version a rule pins, as a rule. */
export function useRuleChecks() {
  return useQuery({
    queryKey: ['rule-checks'],
    queryFn: api.ruleChecks,
    refetchInterval: (query) => (busy(query.state.data?.state) ? AGAIN_MS : false),
  })
}

export function useScriptRuleCheck(scriptId: string, version: number | null) {
  return useQuery({
    queryKey: ['rule-check', 'script', scriptId, version],
    queryFn: () => api.scriptRuleCheck(scriptId, version ?? 1),
    enabled: version !== null,
    refetchInterval: (query) => (busy(query.state.data?.state) ? AGAIN_MS : false),
  })
}

export function useRuleRuleCheck(ruleId: string) {
  return useQuery({
    queryKey: ['rule-check', 'rule', ruleId],
    queryFn: () => api.ruleRuleCheck(ruleId),
    refetchInterval: (query) => (busy(query.state.data?.state) ? AGAIN_MS : false),
  })
}

export function useLuxalgoRuleCheck(slug: string, enabled: boolean) {
  return useQuery({
    queryKey: ['rule-check', 'luxalgo', slug],
    queryFn: () => api.luxalgoRuleCheck(slug),
    enabled,
    retry: false,
    refetchInterval: (query) => (busy(query.state.data?.state) ? AGAIN_MS : false),
  })
}

/** A run test asked: said when refused, the reports read again so the page follows it. */
export function useAskRuleTest(ask: (body: RuleTestAsked) => Promise<RuleChecksState>) {
  const queryClient = useQueryClient()
  const toast = useToast()
  return useMutation({
    mutationFn: ask,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['rule-check'] })
      void queryClient.invalidateQueries({ queryKey: ['rule-checks'] })
    },
    onError: (error) =>
      toast({ title: 'The run test was not asked', description: error instanceof Error ? error.message : String(error), variant: 'error' }),
  })
}
