// "Ask the AI agent" about a row of a list (a position, a signal, a day, an entry of the health): a link that
// opens the agent's panel beside the page (mounted once by the page, without a subject) on a new conversation
// about it. Nothing while the agent is off: the pages keep their rows as they were.

import { ButtonLink, Hint } from 'ioiti-ui'
import { Sparkles } from 'lucide-react'
import { useSearchParams } from 'react-router'

import type { AgentSubject } from '@/lib/types'

import { askAbout } from './model'
import { useAgentState } from './useConversation'

export function AskAgentLink({ subject, label = 'Ask the AI agent', compact = false }: {
  subject: Pick<AgentSubject, 'kind' | 'id' | 'day' | 'at'>
  label?: string
  /** An icon alone, its words in a hint: a table's last column. */
  compact?: boolean
}) {
  const [params] = useSearchParams()
  const state = useAgentState()
  if (!state.data?.enabled) return null
  const to = askAbout(params, subject)
  if (compact) {
    return (
      <Hint content={label}>
        <ButtonLink to={to} size="sm" variant="ghost" aria-label={label}>
          <Sparkles aria-hidden className="size-3.5" />
        </ButtonLink>
      </Hint>
    )
  }
  return (
    <ButtonLink to={to} size="sm" variant="outline" leftIcon={<Sparkles aria-hidden className="size-3.5" />}>
      {label}
    </ButtonLink>
  )
}
