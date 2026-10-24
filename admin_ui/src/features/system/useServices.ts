import { useQuery } from '@tanstack/react-query'

import { api } from '@/lib/api'

import { planRunning } from './restarts'

/**
 * WATA's containers as the control service sees them, with a plan of
 * restarts while one runs: read every two seconds then, as the live events
 * may not reach a page whose admin restarts.
 */
export function useServices(enabled = true) {
  return useQuery({
    queryKey: ['services'],
    queryFn: api.services,
    enabled,
    refetchInterval: (query) => (planRunning(query.state.data?.plan) ? 2_000 : 30_000),
  })
}
