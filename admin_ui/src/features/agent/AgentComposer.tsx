// What is asked of the AI agent: a new conversation (about a run or a script when given), or the next request
// of one, with the model and the effort it goes to (the conversation's last, else the settings'). Sending queues
// it: the admin works on it whether the page stays open or not. Under the field, beside the limits, the size of
// the context window the conversation fills.

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Alert, AlertDescription, PromptComposer } from 'ioiti-ui'
import { useState } from 'react'

import { api } from '@/lib/api'
import type { AgentChoice, AgentConversation, AgentSubject } from '@/lib/types'

import { ChoiceMenus } from './ChoiceMenus'
import { ContextWindow } from './ContextWindow'
import { askRefusal, choiceOf, isUnderWay, limitsSaid, presetsFor, shortModel, unreadBy, writersOf } from './model'
import { conversationKey, useAgentState } from './useConversation'

const MAX_PROMPT = 8000

export function AgentComposer({ conversation, subject, onStarted, className }: {
  /** The conversation the request goes to; none: a new one. */
  conversation?: AgentConversation | null
  /** What a new conversation is about. */
  subject?: Pick<AgentSubject, 'kind' | 'id' | 'version' | 'day' | 'at'> | null
  onStarted?: (id: string) => void
  className?: string
}) {
  const queryClient = useQueryClient()
  const state = useAgentState()
  const [value, setValue] = useState('')
  // A choice made here, for the conversation open (or a new one): sent with its requests, kept by the admin.
  const [choices, setChoices] = useState<Record<string, AgentChoice>>({})
  const key = conversation?.id ?? 'new'
  const choice = choices[key] ?? choiceOf(state.data, conversation)
  const models = state.data?.models ?? []
  const model = models.find((candidate) => candidate.id === choice?.model)
  const choose = (next: AgentChoice) => setChoices((current) => ({ ...current, [key]: next }))
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ['agent'] })
    void queryClient.invalidateQueries({ queryKey: ['agent-conversations'] })
  }
  const send = useMutation({
    mutationFn: (prompt: string) => conversation
      ? api.askAgent(conversation.id, prompt, choice ?? undefined)
      : api.startAgentConversation(prompt, subject ? {
        kind: subject.kind, id: subject.id, version: subject.version ?? undefined, day: subject.day ?? undefined,
        at: subject.at ?? undefined,
      } : undefined, choice ?? undefined),
    onSuccess: (made) => {
      setValue('')
      refresh()
      void queryClient.invalidateQueries({ queryKey: conversationKey(made.id) })
      if (!conversation) onStarted?.(made.id)
    },
  })
  const stop = useMutation({
    mutationFn: () => api.stopAgent(conversation!.id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: conversationKey(conversation!.id) }),
  })
  const busy = !!conversation && isUnderWay(conversation.status)
  const refusal = askRefusal(state.data)
  const kind = subject?.kind ?? conversation?.subject?.kind ?? null
  const fresh = !conversation || conversation.requests.length === 0
  const writers = writersOf(conversation)
  // Its notes another model wrote and this one cannot read: said, as it answers without them.
  const unread = unreadBy(model, writers, models)
  const footer = [unread.length > 0 ? `Without the notes of ${unread.map(shortModel).join(' and ')}` : null, limitsSaid(state.data)]
    .filter(Boolean).join(' · ')

  const controls = choice && model ? (
    <>
      <ChoiceMenus state={state.data} choice={choice} onChoose={choose} writers={writers} />
      <ContextWindow conversation={conversation} model={model} models={models} />
    </>
  ) : undefined

  return (
    <div className={className}>
      <PromptComposer
        label={conversation ? 'Ask the agent again' : 'Ask the AI agent'}
        hideLabel
        value={value}
        onValueChange={setValue}
        onSubmit={(prompt) => send.mutate(prompt)}
        onStop={busy && !stop.isPending ? () => stop.mutate() : undefined}
        busy={busy || send.isPending}
        disabled={!!refusal}
        disabledReason={refusal}
        placeholder={kind === 'run' ? 'Ask about this run…' : kind === 'script' ? 'Ask about this script…' : 'Ask the agent…'}
        suggestions={fresh ? presetsFor(kind) : []}
        controls={controls}
        footer={footer || undefined}
        maxLength={MAX_PROMPT}
      />
      {(send.error || stop.error) && (
        <Alert tone="danger" role="alert" className="mt-2">
          <AlertDescription>{(send.error ?? stop.error)!.message}</AlertDescription>
        </Alert>
      )}
    </div>
  )
}
