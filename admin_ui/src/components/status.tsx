import { Badge, Hint } from 'ioiti-ui'

import { actionLabel } from '@/lib/labels'

// The states of the services, the signed figures and the loading states are ioiti-ui's (StatusBadge,
// SignedValue, QueryState); what is WATA's own is here.

/** Beside the name of what Claude made through the admin's MCP endpoint: a run, a script, a rule, an experiment. */
export function ClaudeBadge({ className }: { className?: string }) {
  return (
    <Hint content="Made by Claude: through the MCP endpoint, or by the AI agent of these pages" wrap>
      <Badge variant="outline" className={className}>
        Claude
      </Badge>
    </Hint>
  )
}

/** Trade direction. Neutral on purpose: long and short are identities, not good or bad. */
export function ActionBadge({ action }: { action: string | null | undefined }) {
  return <Badge variant="outline">{actionLabel(action)}</Badge>
}
