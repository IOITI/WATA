// A rule's protections (GET /api/ta/rules/<id>/protections), for its page and the go-live dialog.

import { useQuery } from '@tanstack/react-query'

import { api } from '@/lib/api'

/** The rule's protections, read again each minute while shown. */
export function useRuleProtections(ruleId: string | undefined, enabled = true) {
  return useQuery({
    queryKey: ['ta-rule-protections', ruleId],
    queryFn: () => api.ruleProtections(ruleId as string),
    enabled: enabled && Boolean(ruleId),
    refetchInterval: 60_000,
    staleTime: 15_000,
  })
}
