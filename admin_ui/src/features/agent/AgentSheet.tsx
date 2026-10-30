// The AI agent beside what it is asked about: a run, a script, or a row of a list (a position, a signal, a
// day, an entry of the health). A sheet that pushes the page aside on a wide screen (over it on a narrow one),
// its conversations about this subject, the one open, and what to ask next. Its state is in the address
// (?agent=<conversation>, ?agent=new, and for a list's row ?about=<kind>:<id>): a link opens it, a reload keeps it.
// While it works, the sheet follows its newest step (useFollowLatest).

import { useQuery } from '@tanstack/react-query'
import {
  ButtonLink,
  Flysheet,
  FlysheetBody,
  FlysheetContent,
  FlysheetDescription,
  FlysheetFooter,
  FlysheetHeader,
  FlysheetTitle,
  QueryState,
} from 'ioiti-ui'
import { ExternalLink, Plus } from 'lucide-react'
import { useRef } from 'react'
import { Link, useSearchParams } from 'react-router'

import { api } from '@/lib/api'
import type { AgentProposal, AgentSubject } from '@/lib/types'

import { AgentComposer } from './AgentComposer'
import { ConversationView } from './ConversationView'
import { LatestButton } from './LatestButton'
import { ABOUT_PARAM, AGENT_PARAM, costText, isUnderWay, NEW, STATUS_LABEL, subjectOf, subjectSaid, withAgent } from './model'
import { useConversation } from './useConversation'
import { useFollowLatest } from './useFollowLatest'

// The conversation open in the accent where the look has one, as a tab shown is.
const CHIP =
  'inline-flex items-center gap-1 rounded-full border border-border px-2.5 py-1 text-xs aria-[current=page]:bg-surface-raised ' +
  'accent:aria-[current=page]:border-brand/30 accent:aria-[current=page]:bg-brand-soft accent:aria-[current=page]:text-brand-text'

export function AgentSheet({ subject: given, blockedFor, onApplied }: {
  /** What the page is about; a list's page leaves it to the address (?about=) or to the conversation open. */
  subject?: AgentSubject
  /** Why a proposal cannot be applied from this page now, if it cannot. */
  blockedFor?: (proposal: AgentProposal) => string | null
  onApplied?: (proposal: AgentProposal) => void
}) {
  const [params, setParams] = useSearchParams()
  const open = params.get(AGENT_PARAM)
  const chosen = open && open !== NEW ? open : null
  const conversation = useConversation(chosen)
  const subject = given ?? conversation.data?.subject ?? subjectOf(params.get(ABOUT_PARAM))
  const listed = useQuery({
    queryKey: ['agent-conversations', subject ? `${subject.kind}:${subject.id}` : 'none'],
    queryFn: () => api.agentConversations(`${subject!.kind}:${subject!.id}`),
    enabled: open !== null && !!subject,
  })
  const close = () => {
    const next = new URLSearchParams(params)
    next.delete(AGENT_PARAM)
    next.delete(ABOUT_PARAM)
    setParams(next, { replace: true })
  }
  const choose = (id: string) => {
    const next = new URLSearchParams(params)
    next.set(AGENT_PARAM, id)
    next.delete(ABOUT_PARAM)
    setParams(next, { replace: true })
  }
  const body = useRef<HTMLDivElement>(null)
  const drawn = useRef<HTMLDivElement>(null)
  const shown = chosen ? conversation.data : undefined
  const follow = useFollowLatest(body, drawn, { id: shown ? shown.id : null, live: !!shown && isUnderWay(shown.status) })
  if (!subject && !chosen) return null

  return (
    <Flysheet open={open !== null} onOpenChange={(value) => { if (!value) close() }}>
      <FlysheetContent side="right" type="push" size="lg" resizable>
        <FlysheetHeader>
          <FlysheetTitle>AI agent</FlysheetTitle>
          <FlysheetDescription>
            {subject ? `${subjectSaid(subject)}: ` : ''}it reads it, runs backtests within its limits and proposes changes you apply.
          </FlysheetDescription>
        </FlysheetHeader>
        <FlysheetBody ref={body} className="space-y-4">
          <nav aria-label="Conversations about it" className="flex flex-wrap gap-1.5">
            <Link to={withAgent(params, NEW)} replace aria-current={open === NEW ? 'page' : undefined} className={CHIP}>
              <Plus aria-hidden className="size-3" /> New
            </Link>
            {(listed.data?.items ?? []).map((item) => (
              <Link key={item.id} to={withAgent(params, item.id)} replace aria-current={item.id === chosen ? 'page' : undefined}
                className={`${CHIP} max-w-56 truncate`}>
                {item.title} <span className="text-muted-foreground">· {STATUS_LABEL[item.status]} · {costText(item.cost_usd)}</span>
              </Link>
            ))}
          </nav>
          {chosen ? (
            <QueryState isPending={conversation.isPending} error={conversation.error} hasData={!!conversation.data}>
              {conversation.data && (
                <div ref={drawn}>
                  <ConversationView conversation={conversation.data} blockedFor={blockedFor} onApplied={onApplied} />
                </div>
              )}
            </QueryState>
          ) : (
            <p className="text-sm text-muted-foreground">
              Ask anything about it, or start from one of the requests below. The agent goes on when you close this, and
              the bell says when it is done.
            </p>
          )}
        </FlysheetBody>
        <FlysheetFooter className="relative block space-y-2">
          {follow.away && <LatestButton onClick={follow.jump} />}
          <AgentComposer conversation={chosen ? conversation.data : null} subject={chosen ? null : subject} onStarted={choose}
            onSent={follow.follow} />
          {chosen && (
            <ButtonLink to={`/agent/${encodeURIComponent(chosen)}`} size="sm" variant="ghost" leftIcon={<ExternalLink className="size-3.5" />}>
              On its own page
            </ButtonLink>
          )}
        </FlysheetFooter>
      </FlysheetContent>
    </Flysheet>
  )
}
