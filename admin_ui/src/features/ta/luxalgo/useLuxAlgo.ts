// What the LuxAlgo Library's pages read of the admin alike: whether LuxAlgo's tools are on and the admin signed in,
// and the Library's families.

import { useQuery } from '@tanstack/react-query'

import { api } from '@/lib/api'
import type { LuxAlgoFamily } from '@/lib/types'

export function useLuxAlgoStatus() {
  return useQuery({ queryKey: ['luxalgo', 'status'], queryFn: api.luxalgoStatus })
}

export function useLuxAlgoFamilies(enabled: boolean) {
  // The Library's families change seldom: read once a session.
  return useQuery({ queryKey: ['luxalgo', 'families'], queryFn: api.luxalgoFamilies, enabled, staleTime: Infinity })
}

export function familyName(families: LuxAlgoFamily[] | undefined, key: string | undefined): string | undefined {
  return families?.find((family) => family.key === key)?.name ?? key
}

/** WATA's checks of LuxAlgo's indicators, read again every 5 s while some wait or run. */
export function useLuxAlgoChecks() {
  return useQuery({
    queryKey: ['luxalgo', 'compat'],
    queryFn: api.luxalgoCompat,
    refetchInterval: (query) => (query.state.data?.checks.queued?.length || query.state.data?.checks.checking ? 5_000 : false),
  })
}
