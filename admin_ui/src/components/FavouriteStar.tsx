// A thing among the favourites, or not: its star, pressed to put it there or take it out. The backtests' runs and the
// Technical Analysis scripts have one.

import { cn, Hint } from 'ioiti-ui'
import { Star } from 'lucide-react'

export function FavouriteStar({ on, name, onToggle, className }: { on: boolean; name: string; onToggle: () => void; className?: string }) {
  return (
    <Hint content={on ? 'A favourite: press to take it out' : 'Add it to favourites'}>
      <button
        type="button"
        aria-pressed={on}
        aria-label={on ? `Remove “${name}” from favourites` : `Add “${name}” to favourites`}
        onClick={onToggle}
        className={cn(
          'inline-grid size-6 place-items-center rounded-md transition-colors hover:bg-surface-hover focus-ring',
          on ? 'text-warning' : 'text-muted-foreground hover:text-foreground',
          className,
        )}
      >
        <Star className={cn('size-4', on && 'fill-current')} aria-hidden />
      </button>
    </Hint>
  )
}
