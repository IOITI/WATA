// What the next request of a conversation would put in the model's context window. The button says the size
// the last answer left (no count); opened, the API counts it as it would be sent (GET …/context): a meter of
// all the window holds, its parts as bars, what context editing leaves out and what the last answer used.

import { useQuery } from '@tanstack/react-query'
import { BarList, Button, formatNumber, formatTime, Meter, Popover, QueryState } from 'ioiti-ui'
import { Gauge } from 'lucide-react'
import { useState } from 'react'

import { useSession } from '@/features/auth/auth-context'
import { api } from '@/lib/api'
import type { AgentContext, AgentConversation, AgentLastCall, AgentModel } from '@/lib/types'

import { contextItems, contextTone, contextUsed, costText, tokensText } from './model'

const HOW_SAID: Record<NonNullable<AgentContext['how']>, string> = {
  counted: 'Counted by the Claude API as the next request would be sent. The system prompt, the tools and the ' +
    'thinking are its counts; the rest of the conversation is split by its length.',
  estimated: 'Estimated from the last answer: the Claude API could not count it now.',
}

function LastCall({ last, models }: { last: AgentLastCall; models: AgentModel[] }) {
  const { timezone } = useSession()
  const label = models.find((model) => model.id === last.model)?.label ?? last.model
  const dropped = Object.values(last.dropped_notes ?? {}).reduce((sum, count) => sum + count, 0)
  return (
    <div className="space-y-1 border-t border-border pt-2.5 text-xs text-text-secondary">
      <p className="font-medium text-foreground">
        The last answer{label ? `, ${label}` : ''}{last.at ? `, ${formatTime(last.at, timezone)}` : ''}
      </p>
      <p>
        {formatNumber(last.prompt)} tokens in: {formatNumber(last.cache_read)} read from the cache,{' '}
        {formatNumber(last.cache_write)} written to it, {formatNumber(last.uncached)} not cached.{' '}
        {formatNumber(last.output)} out{last.thinking ? `, ${formatNumber(last.thinking)} of them thinking` : ''}.
        {last.usd !== null && ` ${costText(last.usd)}.`}
      </p>
      {last.cleared_tokens && (
        <p>Context editing cleared {formatNumber(last.cleared_tokens)} tokens of {formatNumber(last.cleared_tool_uses)} old tool results.</p>
      )}
      {dropped > 0 && <p>The model left out {dropped} note{dropped === 1 ? '' : 's'} it could not read.</p>}
    </div>
  )
}

function Details({ view, models }: { view: AgentContext; models: AgentModel[] }) {
  const total = view.total ?? 0
  const ratio = total / view.window
  return (
    <div className="space-y-3">
      <div className="space-y-1.5">
        <p className="text-sm tabular-nums text-foreground">
          <span className="font-medium">{formatNumber(total)}</span> of {formatNumber(view.window)} tokens
          <span className="text-muted-foreground"> · {ratio < 0.01 && total > 0 ? 'under 1' : Math.round(ratio * 100)}%</span>
        </p>
        <Meter ratio={ratio} tone={contextTone(ratio)} label={`The context window of ${view.label}, used`}
          scale={['0', tokensText(view.window)]} />
      </div>
      <BarList items={contextItems(view)} label="What the context window holds" max={total} />
      {view.how && <p className="text-xs text-muted-foreground">{HOW_SAID[view.how]}</p>}
      {view.cleared ? (
        <p className="text-xs text-text-secondary">
          Context editing leaves out {formatNumber(view.cleared)} tokens of old tool results: past{' '}
          {formatNumber(view.clearing.from_tokens)} tokens, it keeps the last {view.clearing.keep_tool_uses} calls&apos; results.
        </p>
      ) : null}
      {view.unread.length > 0 && (
        <p className="text-xs text-text-secondary">
          {view.label} cannot read the notes {view.unread.join(' and ')} wrote here: they are left out of its requests.
        </p>
      )}
      <p className="text-xs text-muted-foreground">Each answer may take up to {formatNumber(view.answer_room)} tokens more.</p>
      {view.last && <LastCall last={view.last} models={models} />}
    </div>
  )
}

export function ContextWindow({ conversation, model, models }: {
  conversation?: AgentConversation | null
  /** The model the next request goes to: its window, and its count. */
  model: AgentModel | undefined
  models: AgentModel[]
}) {
  const [open, setOpen] = useState(false)
  const view = useQuery({
    // Counted again once the conversation has grown, or for another model.
    queryKey: ['agent-context', conversation?.id ?? 'new', model?.id, conversation?.context?.at ?? null],
    queryFn: () => api.agentContext(conversation?.id ?? null, model!.id),
    enabled: open && !!model,
    staleTime: 60_000,
  })
  const used = contextUsed(conversation?.context?.tokens, model?.window)
  const said = used
    ? `Context window: ${formatNumber(conversation!.context!.tokens)} of ${formatNumber(model!.window)} tokens`
    : 'Context window'
  return (
    <Popover
      label="Context window"
      open={open}
      onOpenChange={setOpen}
      side="top"
      align="start"
      className="w-[min(22rem,calc(100vw-2rem))] p-3"
      trigger={
        <Button size="sm" variant="ghost" aria-label={said} leftIcon={<Gauge aria-hidden className="size-3.5" />}>
          <span className="tabular-nums">{used?.text ?? 'Context'}</span>
        </Button>
      }
    >
      <div className="space-y-3">
        <div className="flex items-baseline justify-between gap-3">
          <p className="text-sm font-semibold text-foreground">Context window</p>
          {model && <p className="text-xs text-muted-foreground">{model.label}</p>}
        </div>
        <QueryState isPending={view.isPending} error={view.error} hasData={!!view.data}>
          {view.data && <Details view={view.data} models={models} />}
        </QueryState>
      </div>
    </Popover>
  )
}
