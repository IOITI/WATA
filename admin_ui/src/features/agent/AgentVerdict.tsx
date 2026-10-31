// What the AI agent last concluded on a run, a signal rule, a trading day or a position (set_verdict), shown on
// that thing's page in one line beside a link to the conversation it came from. Said to be from before when the
// thing changed since (a rule that runs something else). Nothing while the agent is off.

import { formatDateTime, StatusBadge } from 'ioiti-ui'
import { Sparkles } from 'lucide-react'
import { Link } from 'react-router'

import { useSession } from '@/features/auth/auth-context'

import type { AgentVerdict as Verdict, AgentVerdictTone } from '@/lib/types'

import { VERDICT_SAID, verdictBefore } from './model'
import { useVerdicts } from './useConversation'

const BADGE: Record<AgentVerdictTone, 'ok' | 'warning' | 'down' | 'unknown'> = {
  positive: 'ok', mixed: 'warning', negative: 'down', neutral: 'unknown',
}

export function VerdictLine({ verdict, changedAt, className }: {
  verdict: Verdict
  /** When the thing last changed what it is: a verdict older than that is said to be from before. */
  changedAt?: string | null
  className?: string
}) {
  const { timezone } = useSession()
  const before = verdictBefore(verdict, changedAt)
  return (
    <p className={`flex flex-wrap items-center gap-x-2 gap-y-1 text-sm ${className ?? ''}`}>
      <Sparkles aria-hidden className="size-3.5 shrink-0 text-muted-foreground" />
      <span className="sr-only">The AI agent's verdict:</span>
      <StatusBadge status={BADGE[verdict.tone]} label={VERDICT_SAID[verdict.tone]} />
      <span className={before ? 'text-muted-foreground' : undefined}>{verdict.line}</span>
      <Link className="text-xs text-muted-foreground underline underline-offset-2" to={`/agent/${encodeURIComponent(verdict.conversation)}`}>
        {before ? 'before its last change, ' : ''}{formatDateTime(verdict.at, timezone, false)}
      </Link>
    </p>
  )
}

/** The verdict on one thing, when the agent gave one. */
export function AgentVerdict({ about, changedAt, className }: { about: string; changedAt?: string | null; className?: string }) {
  const verdicts = useVerdicts([about])
  const verdict = verdicts.data?.items[about]
  return verdict ? <VerdictLine verdict={verdict} changedAt={changedAt} className={className} /> : null
}
