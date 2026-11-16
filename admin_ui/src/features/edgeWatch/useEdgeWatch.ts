// Edge Watch's judgements (GET /api/edge-watch, /api/edge-watch/<owner>): read again each minute, and at once when
// the server says a verdict changed (the live events' edge_watch_changed, which marks them stale).

import { useQuery } from '@tanstack/react-query'

import { api, isNotFound } from '@/lib/api'

const EVERY = 60_000

/** Every owner's verdict, without its cone. */
export function useEdgeWatch(enabled: boolean) {
  return useQuery({ queryKey: ['edge-watch'], queryFn: api.edgeWatch, enabled, refetchInterval: EVERY, staleTime: 15_000 })
}

/** One owner whole (``live`` or a rule's id); null while it has no reference run. */
export function useEdgeWatchOwner(owner: string, enabled: boolean) {
  return useQuery({
    queryKey: ['edge-watch', owner],
    queryFn: async () => {
      try {
        return await api.edgeWatchOwner(owner)
      } catch (error) {
        if (isNotFound(error)) return null
        throw error
      }
    },
    enabled,
    refetchInterval: EVERY,
    staleTime: 15_000,
  })
}
