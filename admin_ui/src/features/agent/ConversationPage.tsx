// One conversation of the AI agent, on its own page: every request, what the agent did and proposed, and
// the next request. It goes on when the page is closed; the bell says when it is done. While it works, the page
// follows its newest step (useFollowLatest).

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Alert, AlertDescription, Badge, ButtonLink, Hint, InlineEdit, PageHeader, QueryState, useMainScroll } from 'ioiti-ui'
import { ExternalLink } from 'lucide-react'
import { useRef } from 'react'
import { useParams } from 'react-router'

import { NotFoundState } from '@/components/NotFoundState'
import { api, isNotFound } from '@/lib/api'

import { AgentComposer } from './AgentComposer'
import { heldBy } from './attachments'
import { ConversationView } from './ConversationView'
import { LatestButton } from './LatestButton'
import { TaskPanel } from './TaskPanel'
import { costText, isUnderWay, OUTCOME_LABEL, STATUS_LABEL, statusTone, subjectLink, subjectSaid, withAgent } from './model'
import { conversationKey, useConversation } from './useConversation'
import { useFollowLatest } from './useFollowLatest'

export function ConversationPage() {
  const { conversationId = '' } = useParams()
  const queryClient = useQueryClient()
  const conversation = useConversation(conversationId)
  const rename = useMutation({
    mutationFn: (title: string) => api.renameAgentConversation(conversationId, title),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: conversationKey(conversationId) })
      void queryClient.invalidateQueries({ queryKey: ['agent-conversations'] })
    },
  })
  const data = conversation.data
  const drawn = useRef<HTMLDivElement>(null)
  const follow = useFollowLatest(useMainScroll(), drawn, { id: data?.id ?? null, live: !!data && isUnderWay(data.status) })

  if (isNotFound(conversation.error)) {
    return (
      <NotFoundState
        title="No such conversation"
        description="The AI agent has no conversation at this address: it may have been removed, or the address is mistyped."
        suggestions={[{ label: 'AI agent', href: '/agent', description: 'Every conversation, and a new one' }]}
      />
    )
  }
  const last = data?.requests[data.requests.length - 1]

  return (
    <div className="space-y-6">
      <PageHeader
        title={data?.title ?? 'Conversation'}
        description={
          data && (
            <span className="inline-flex flex-wrap items-center gap-2">
              <Badge variant="soft" tone={statusTone(data.status, last?.status)}>
                {data.status === 'idle' && last ? OUTCOME_LABEL[last.status] : STATUS_LABEL[data.status]}
              </Badge>
              {data.subject && (
                <span>
                  About {subjectSaid(data.subject)}
                  {heldBy(data).length > 1 && ` and ${heldBy(data).length - 1} more`}
                </span>
              )}
              <span>{costText(data.cost_usd)} so far</span>
            </span>
          )
        }
        actions={
          data?.subject && (
            <Hint content="Its page, with this conversation beside it">
              <ButtonLink to={`${subjectLink(data.subject)}${withAgent(new URLSearchParams(), data.id)}`}
                size="sm" variant="outline" leftIcon={<ExternalLink className="size-3.5" />}>
                Beside the {data.subject.kind}
              </ButtonLink>
            </Hint>
          )
        }
      />
      <QueryState isPending={conversation.isPending} error={conversation.error} hasData={!!data}>
        {data && (
          <div className="mx-auto max-w-4xl space-y-6">
            <InlineEdit label="Title" value={data.title} onSave={(title) => rename.mutateAsync(title).then(() => undefined)} />
            {data.task && <TaskPanel conversation={data} task={data.task} />}
            <div ref={drawn}>
              <ConversationView conversation={data} />
            </div>
            {/* The field floats over what scrolls under it, with no band of its own: a band of the page's colour
                was a box on the glass material's washes. Above the activity's step marks (z-10). */}
            <div className="sticky bottom-3 z-20">
              {follow.away && <LatestButton onClick={follow.jump} />}
              <AgentComposer conversation={data} floating onSent={follow.follow} />
            </div>
            {rename.error && (
              <Alert tone="danger" role="alert"><AlertDescription>{rename.error.message}</AlertDescription></Alert>
            )}
          </div>
        )}
      </QueryState>
    </div>
  )
}
