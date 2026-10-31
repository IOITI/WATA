// "Tell me on Telegram": what becomes of a request, or of a research task, said on Telegram once it ends (its first
// lines and where to read the rest), as the daily debrief is said. Disabled while no Telegram bot is set.

import { Button, Hint } from 'ioiti-ui'
import { Bell, BellOff } from 'lucide-react'

import type { AgentState } from '@/lib/types'

import { tellSaid } from './model'

export function TellToggle({ state, tell, onChange, task = false }: {
  state: AgentState | undefined
  tell: boolean
  onChange: (tell: boolean) => void
  /** A research task: told at its end, not at each request. */
  task?: boolean
}) {
  const unset = state?.telegram === false
  return (
    <Hint content={tellSaid(state, tell, task)} wrap={unset}>
      <Button
        type="button"
        size="sm"
        variant="ghost"
        aria-pressed={tell && !unset}
        aria-label="Tell me on Telegram"
        disabled={unset}
        onClick={() => onChange(!tell)}
        leftIcon={tell && !unset ? <Bell aria-hidden className="size-3.5" /> : <BellOff aria-hidden className="size-3.5" />}
      >
        <span className="sr-only sm:not-sr-only">{tell && !unset ? 'Telegram' : 'No Telegram'}</span>
      </Button>
    </Hint>
  )
}
