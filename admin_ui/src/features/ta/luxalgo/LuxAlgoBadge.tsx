import { Badge, Hint } from 'ioiti-ui'

import type { ScriptOrigin } from '@/lib/types'

import { originSaid } from './luxalgoModel'

/** A script taken from LuxAlgo's Library, or whose header says it is LuxAlgo's: its licence on hover, focus and tap. */
export function LuxAlgoBadge({ origin, className }: { origin: ScriptOrigin; className?: string }) {
  return (
    <Hint content={originSaid(origin)} wrap>
      <Badge variant="outline" className={className}>
        LuxAlgo
      </Badge>
    </Hint>
  )
}
