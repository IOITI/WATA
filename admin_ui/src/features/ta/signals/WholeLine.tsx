import type { TaRuleState } from '@/lib/types'

import { wholeLine } from './ruleModel'

/** How the engine runs a rule's new bars when they do not continue its run: a run let go is a warning. */
export function WholeLine({ figures, timeZone }: { figures: TaRuleState['figures']; timeZone: string }) {
  const line = wholeLine(figures, timeZone)
  if (!line) return null
  return <p className={line.letGo ? 'text-warning' : 'text-muted-foreground'}>{line.text}</p>
}
