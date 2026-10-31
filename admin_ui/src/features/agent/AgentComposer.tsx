// What is asked of the AI agent: a new conversation (about a run or a script when given), or the next request
// of one, with the model and the effort it goes to (the conversation's last, else the settings'), and whether its
// answer is said on Telegram once written. What it goes with is attached above the field: WATA's things from the
// paperclip, files chosen there, dropped on the field or pasted into it (useAttachments). Sending queues it: the admin
// works on it whether the page stays open or not. Under the field, beside the limits, the size of the context window
// the conversation fills. Floating, it stands over what scrolls under it, at a page's foot.

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Alert, AlertDescription, PromptComposer } from 'ioiti-ui'
import { useMemo, useState } from 'react'

import { api } from '@/lib/api'
import type { AgentChoice, AgentConversation, AgentSubject } from '@/lib/types'

import { AttachMenu } from './AttachMenu'
import { PendingChips } from './AttachedChips'
import { comparePreset, heldBy, sendBlocked } from './attachments'
import { ChoiceMenus } from './ChoiceMenus'
import { ContextWindow } from './ContextWindow'
import { TellToggle } from './TellToggle'
import { askRefusal, choiceOf, isUnderWay, limitsSaid, presetsFor, shortModel, unreadBy, writersOf } from './model'
import { useAttachments } from './useAttachments'
import { conversationKey, useAgentState } from './useConversation'

const MAX_PROMPT = 8000

export function AgentComposer({ conversation, subject, onStarted, onSent, floating, className }: {
  /** The conversation the request goes to; none: a new one. */
  conversation?: AgentConversation | null
  /** What a new conversation is about: the page's, shown first and kept. */
  subject?: AgentSubject | null
  onStarted?: (id: string) => void
  /** A request was sent: the page goes to its foot, where it will be. */
  onSent?: () => void
  /** At the foot of a page, over what scrolls under it, with no band behind it. */
  floating?: boolean
  className?: string
}) {
  const queryClient = useQueryClient()
  const state = useAgentState()
  const [value, setValue] = useState('')
  // A choice made here, for the conversation open (or a new one): sent with its requests, kept by the admin.
  const [choices, setChoices] = useState<Record<string, AgentChoice>>({})
  // Said on Telegram once answered: the operator's word for each request, off unless given.
  const [tell, setTell] = useState(false)
  const held = useMemo(() => heldBy(conversation), [conversation])
  const fixed = conversation ? null : subject ?? null
  const attachments = useAttachments({ fixed, held })
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
      ? api.askAgent(conversation.id, prompt, choice ?? undefined, tell, attachments.attach)
      : api.startAgentConversation(prompt, fixed ? {
        kind: fixed.kind, id: fixed.id, version: fixed.version ?? undefined, day: fixed.day ?? undefined,
        at: fixed.at ?? undefined,
      } : undefined, choice ?? undefined, tell, attachments.attach),
    onSuccess: (made) => {
      setValue('')
      setTell(false)
      attachments.clear()
      refresh()
      void queryClient.invalidateQueries({ queryKey: conversationKey(made.id) })
      if (!conversation) onStarted?.(made.id)
      onSent?.()
    },
  })
  const stop = useMutation({
    mutationFn: () => api.stopAgent(conversation!.id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: conversationKey(conversation!.id) }),
  })
  const busy = !!conversation && isUnderWay(conversation.status)
  const refusal = askRefusal(state.data)
  // Sent only once its files are uploaded, and to a model that reads them.
  const blocked = sendBlocked(attachments.pending, model, held)
  const kind = fixed?.kind ?? attachments.subjects[0]?.kind ?? conversation?.subject?.kind ?? null
  const fresh = !conversation || conversation.requests.length === 0
  const compare = comparePreset(attachments.subjects)
  const writers = writersOf(conversation)
  // Its notes another model wrote and this one cannot read: said, as it answers without them.
  const unread = unreadBy(model, writers, models)
  const footer = [unread.length > 0 ? `Without the notes of ${unread.map(shortModel).join(' and ')}` : null, limitsSaid(state.data)]
    .filter(Boolean).join(' · ')
  // About one thing, the field names it; about several, it names none of them.
  const placeholder = !fixed && attachments.pending.length > 0 ? 'Ask about what is attached…'
    : held.length > 1 ? 'Ask the agent…'
      : kind === 'run' ? 'Ask about this run…' : kind === 'script' ? 'Ask about this script…'
        : kind === 'luxalgo' ? 'Ask about this indicator…' : 'Ask the agent…'

  const controls = choice && model ? (
    <>
      <AttachMenu attached={attachments.keys} room={attachments.room} onAttach={attachments.addItem}
        onFiles={attachments.addFiles} disabled={!!refusal} />
      <ChoiceMenus state={state.data} choice={choice} onChoose={choose} writers={writers} />
      <TellToggle state={state.data} tell={tell} onChange={setTell} />
      <ContextWindow conversation={conversation} model={model} models={models} />
    </>
  ) : undefined
  const chips = fixed || attachments.pending.length > 0
    ? <PendingChips fixed={fixed} pending={attachments.pending} onRemove={attachments.remove} />
    : undefined
  const said = blocked ?? attachments.leftOut

  return (
    <div className={className}>
      <PromptComposer
        label={conversation ? 'Ask the agent again' : 'Ask the AI agent'}
        hideLabel
        value={value}
        onValueChange={setValue}
        onSubmit={(prompt) => send.mutate(prompt)}
        onStop={busy && !stop.isPending ? () => stop.mutate() : undefined}
        busy={busy || send.isPending || !!blocked}
        disabled={!!refusal}
        disabledReason={refusal}
        placeholder={placeholder}
        suggestions={fresh ? [...presetsFor(kind, { luxalgo: !!state.data?.luxalgo }), ...(compare ? [compare] : [])] : []}
        controls={controls}
        attachments={chips}
        onFiles={attachments.addFiles}
        footer={footer || undefined}
        maxLength={MAX_PROMPT}
        floating={floating}
      />
      {said && <p role="status" className="mt-1.5 text-xs text-muted-foreground">{said}</p>}
      {(send.error || stop.error) && (
        <Alert tone="danger" role="alert" className="mt-2">
          <AlertDescription>{(send.error ?? stop.error)!.message}</AlertDescription>
        </Alert>
      )}
    </div>
  )
}
