// A conversation of the AI agent as a page follows it: read whole, then kept up to date by its stream
// (/api/agent/conversations/<id>/stream): "changed" reads it again, "delta" adds a piece of text where it
// belongs, "writing" says a tool call's input growing. The admin works on it whether a page follows or not.

import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect } from 'react'

import { api } from '@/lib/api'
import type { AgentConversation } from '@/lib/types'

import { foldDelta, foldWriting } from './model'
import type { Delta } from './model'

// Several steps in a row (a tool's start and end): read once.
const REFRESH_AFTER_MS = 150

export function conversationKey(id: string | null) {
  return ['agent-conversation', id] as const
}

export function useConversation(id: string | null) {
  const queryClient = useQueryClient()
  const query = useQuery({
    queryKey: conversationKey(id),
    queryFn: () => api.agentConversation(id!),
    enabled: !!id,
  })

  useEffect(() => {
    if (!id || typeof EventSource === 'undefined') return
    const key = conversationKey(id)
    const source = new EventSource(`/api/agent/conversations/${encodeURIComponent(id)}/stream`)
    let timer: ReturnType<typeof setTimeout> | undefined
    const refresh = () => {
      if (timer !== undefined) return
      timer = setTimeout(() => {
        timer = undefined
        void queryClient.invalidateQueries({ queryKey: key })
      }, REFRESH_AFTER_MS)
    }
    const onDelta = (event: MessageEvent<string>) => {
      const delta = JSON.parse(event.data) as Delta
      let stale = false
      queryClient.setQueryData<AgentConversation>(key, (current) => {
        if (!current) return current
        const folded = foldDelta(current.pending, delta)
        stale = folded.stale
        return { ...current, pending: folded.pending }
      })
      if (stale) refresh()
    }
    const onWriting = (event: MessageEvent<string>) => {
      const writing = JSON.parse(event.data) as { request: number; tool: string; chars: number }
      queryClient.setQueryData<AgentConversation>(key, (current) =>
        current ? { ...current, pending: foldWriting(current.pending, writing.request, writing.tool, writing.chars) } : current)
    }
    // Connected again after a cut: what was missed is read whole.
    source.addEventListener('connected', refresh)
    source.addEventListener('changed', refresh)
    source.addEventListener('delta', onDelta)
    source.addEventListener('writing', onWriting)
    return () => {
      source.close()
      if (timer !== undefined) clearTimeout(timer)
    }
  }, [id, queryClient])

  return query
}

/** Whether the agent is on, its key set, its month's spending and its limits. */
export function useAgentState() {
  return useQuery({ queryKey: ['agent'], queryFn: api.agent, staleTime: 10_000 })
}

