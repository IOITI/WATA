// What the LuxAlgo Library's pages read of the admin alike: whether LuxAlgo's tools are on and the admin signed in,
// the Library's families, and WATA's checks of its indicators.

import { useQuery } from '@tanstack/react-query'

import { api } from '@/lib/api'
import type { LuxAlgoCompat, LuxAlgoFamily } from '@/lib/types'

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

/** WATA's newest check of one indicator: the checks' own, once one asked here is done, else what the page read. */
export function useIndicatorCheck(slug: string, read: LuxAlgoCompat | null | undefined): LuxAlgoCompat | null {
  const checks = useLuxAlgoChecks()
  return checks.data?.items.find((item) => item.slug === slug) ?? read ?? null
}
