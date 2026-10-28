// A conversation with the AI agent, request by request: what was asked (and shared), what the agent did
// (ioiti-ui's AIAgentActivity), its answer as it streams (AIResponse), what it proposed, how it ended.

import { useMutation, useQueryClient } from '@tanstack/react-query'
import {
  AIAgentActivity,
  AIResponse,
  AIResponseActions,
  AIResponseContent,
  AIResponseHeader,
  AIResponseSources,
  AIResponseToolsSummary,
  Alert,
  AlertDescription,
  Button,
  formatDateTime,
  formatTime,
} from 'ioiti-ui'
import { RotateCw } from 'lucide-react'
import { Link } from 'react-router'

import { useSession } from '@/features/auth/auth-context'
import { api } from '@/lib/api'
import type { AgentConversation, AgentProposal, AgentRequest } from '@/lib/types'

import {
  activityItems,
  answerOf,
  costText,
  isUnderWay,
  outcomeSaid,
  shortModel,
  sourcesOf,
  subjectLink,
  subjectSaid,
  toolsOf,
} from './model'
import { ProposalCard } from './ProposalCard'
import { conversationKey, useAgentState } from './useConversation'

function RequestView({ conversation, request, last, blockedFor, onApplied }: {
  conversation: AgentConversation
  request: AgentRequest
  last: boolean
  blockedFor?: (proposal: AgentProposal) => string | null
  onApplied?: (proposal: AgentProposal) => void
}) {
  const { timezone } = useSession()
  const queryClient = useQueryClient()
  const refresh = () => queryClient.invalidateQueries({ queryKey: conversationKey(conversation.id) })
  // Asked again as it was: on its model, at its effort.
  const ask = useMutation({
    mutationFn: (prompt: string) => api.askAgent(conversation.id, prompt,
      request.model && request.effort ? { model: request.model, effort: request.effort } : undefined),
    onSuccess: refresh,
  })
  const models = useAgentState().data?.models ?? []
  const labelOf = (id: string | null | undefined) => (id ? models.find((candidate) => candidate.id === id)?.label ?? id : undefined)
  const retry = useMutation({ mutationFn: () => api.retryAgent(conversation.id), onSuccess: refresh })
  const feedback = useMutation({
    mutationFn: (value: 'like' | 'dislike' | null) => api.agentFeedback(conversation.id, request.index, value),
  })
  const pending = last ? conversation.pending : null
  const items = activityItems(request, pending, (iso) => formatTime(iso, timezone))
  const answer = answerOf(request, pending)
  const running = isUnderWay(request.status)
  const tools = toolsOf(request)
  const proposals = conversation.proposals.filter((proposal) => request.proposals.includes(proposal.id))
  // The model that answered (a fallback may answer for the one asked), and the one asked with its effort.
  const model = labelOf(request.answer?.model ?? request.model ?? conversation.model)
  const asked = request.model ? `${shortModel(labelOf(request.model)!)}${request.effort ? ` · ${request.effort} effort` : ''}` : model

  return (
    <article aria-label={`Request ${request.index + 1}`} className="space-y-3">
      {request.by === 'task' ? (
        // A research task's next request: asked by the admin, not the operator.
        <p className="text-center text-xs text-muted-foreground">
          {request.prompt}
          {request.asked_at && <> · {formatDateTime(request.asked_at, timezone, false)}</>}
        </p>
      ) : (
        <div className="ml-auto max-w-[min(42rem,100%)] space-y-1.5 rounded-2xl bg-surface-raised px-4 py-3">
          {request.shared && (
            <p className="text-xs text-muted-foreground">
              Shared: <Link className="underline" to={subjectLink(request.shared)}>{subjectSaid(request.shared)}</Link>
            </p>
          )}
          <p className="whitespace-pre-wrap text-sm text-foreground">{request.prompt}</p>
          {request.asked_at && <p className="text-xs text-muted-foreground">{formatDateTime(request.asked_at, timezone, false)}</p>}
        </div>
      )}
      {items.length > 0 && (
        <AIAgentActivity
          activities={items}
          isRunning={running}
          title="What it did"
          agentName={asked}
          summary={`${tools.length} tool call${tools.length === 1 ? '' : 's'}`}
          inputLabel="Given"
          outputLabel="Answered"
        />
      )}
      <AIResponse
        content={answer.content}
        status={answer.status}
        sources={sourcesOf(request, conversation.proposals)}
        feedback={request.feedback}
        onFeedback={running ? undefined : (value) => feedback.mutate(request.feedback === value ? null : value)}
        onRegenerate={last && !running ? () => ask.mutate(request.prompt) : undefined}
      >
        <AIResponseHeader title="Claude" modelBadge={model} timestamp={request.ended_at ? formatTime(request.ended_at, timezone) : undefined} />
        <AIResponseContent />
        <AIResponseToolsSummary toolCount={tools.length} tools={tools} />
        <AIResponseSources />
        <AIResponseActions />
      </AIResponse>
      {!running && (
        <p className="text-xs text-muted-foreground">
          {outcomeSaid(request.status, request.detail)}
          {' · '}This request: {costText(request.cost_usd)}
        </p>
      )}
      {last && request.status === 'failed' && conversation.status === 'failed' && (
        <Button size="sm" variant="outline" leftIcon={<RotateCw className="size-3.5" />} isLoading={retry.isPending} onClick={() => retry.mutate()}>
          Take it up again
        </Button>
      )}
      {(ask.error || retry.error) && (
        <Alert tone="danger" role="alert">
          <AlertDescription>{(ask.error ?? retry.error)!.message}</AlertDescription>
        </Alert>
      )}
      {proposals.map((proposal) => (
        <ProposalCard key={proposal.id} conversationId={conversation.id} proposal={proposal} proposals={conversation.proposals}
          blockedReason={blockedFor?.(proposal) ?? null} onApplied={onApplied} />
      ))}
    </article>
  )
}

export function ConversationView({ conversation, blockedFor, onApplied }: {
  conversation: AgentConversation
  /** Why a proposal cannot be applied from this page now, if it cannot. */
  blockedFor?: (proposal: AgentProposal) => string | null
  onApplied?: (proposal: AgentProposal) => void
}) {
  return (
    <div className="space-y-8">
      {conversation.requests.map((request, index) => (
        <RequestView key={request.index} conversation={conversation} request={request}
          last={index === conversation.requests.length - 1} blockedFor={blockedFor} onApplied={onApplied} />
      ))}
    </div>
  )
}
