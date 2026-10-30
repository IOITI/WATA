// The way back to the newest step while the agent works, once the operator scrolled up: centred over the foot of
// what scrolls, just above the field to ask the agent.

import { Button } from 'ioiti-ui'
import { ArrowDown } from 'lucide-react'

export function LatestButton({ onClick }: { onClick: () => void }) {
  return (
    <div className="pointer-events-none absolute inset-x-0 -top-11 flex justify-center">
      <Button size="sm" variant="outline" onClick={onClick} leftIcon={<ArrowDown aria-hidden className="size-3.5" />}
        className="pointer-events-auto rounded-full shadow-[var(--shadow-elevated)] glass-float">
        Latest
      </Button>
    </div>
  )
}
