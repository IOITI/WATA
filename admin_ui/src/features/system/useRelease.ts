import { useQuery } from '@tanstack/react-query'

import { api } from '@/lib/api'

/**
 * The release each container runs and what waits for the newest, read every 30 seconds, and every two while
 * an apply runs: the live events may not reach a page whose admin is being recreated.
 */
export function useRelease(enabled = true) {
  return useQuery({
    queryKey: ['release'],
    queryFn: api.release,
    enabled,
    refetchInterval: (query) => (query.state.data?.applying ? 2_000 : 30_000),
  })
}
