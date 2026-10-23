import type { ReactElement, ReactNode } from 'react'

import { DirectionalTooltip } from '@/components/ui/directional-tooltip'
import type { TooltipSide } from '@/components/ui/directional-tooltip'

interface HintProps {
  content: ReactNode
  children: ReactElement
  side?: TooltipSide
  /**
   * For what takes no focus of its own: a disabled control (which takes no pointer either: the
   * wrapper gets its hover and its tap) or a plain text. The wrapper then takes the focus, so that a
   * keyboard reaches the hint too.
   */
  wrap?: boolean
  /** The classes of the span around it: a full-width field keeps its width (`flex w-full`). */
  className?: string
}

/**
 * What a `title` said, shown on hover, on keyboard focus and on a tap, and read by screen readers:
 * a title shows on none of a phone, a keyboard or a disabled control. EasyUI's tooltip.
 */
export function Hint({ content, children, side = 'top', wrap = false, className }: HintProps) {
  // A wrapper with nothing to say would be a stop of the keyboard for nothing.
  const said = content !== null && content !== undefined && content !== false && content !== ''
  return (
    <DirectionalTooltip content={content} side={side} wrapperClassName={className}>
      {wrap && said ? (
        <span tabIndex={0} className="inline-flex rounded-md [&>:disabled]:pointer-events-none">
          {children}
        </span>
      ) : (
        children
      )}
    </DirectionalTooltip>
  )
}
