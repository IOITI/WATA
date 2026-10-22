import { CircleCheck, CircleDashed, CircleX } from 'lucide-react'

import type { Check, CheckState } from '@/lib/checks'
import { cn } from '@/lib/utils'

const LOOK: Record<CheckState, { icon: typeof CircleCheck; className: string; said: string }> = {
  pass: { icon: CircleCheck, className: 'text-positive', said: 'Passed' },
  fail: { icon: CircleX, className: 'text-negative', said: 'Failed' },
  open: { icon: CircleDashed, className: 'text-muted-foreground', said: 'Not checked' },
}

/** A check's outcome as an icon, named for a screen reader unless what it stands beside says it already. */
export function CheckIcon({ state, named = true }: { state: CheckState; named?: boolean }) {
  const Icon = LOOK[state].icon
  return (
    <Icon
      className={cn('mt-0.5 size-4 shrink-0', LOOK[state].className)}
      aria-label={named ? LOOK[state].said : undefined}
      aria-hidden={named ? undefined : true}
    />
  )
}

/**
 * Checks and what each found, an icon telling passed, failed and open apart (and
 * named for a screen reader). A check with an address leads to what says more of it.
 */
export function CheckList<T extends Check>({
  checks,
  label,
  hrefOf,
  className,
}: {
  checks: readonly T[]
  label: string
  hrefOf?: (check: T) => string | null
  className?: string
}) {
  return (
    <ul className={cn('grid gap-x-6 gap-y-2.5 text-sm', className)} aria-label={label}>
      {checks.map((check) => {
        const href = hrefOf?.(check) ?? null
        return (
          <li key={check.id} className="flex items-start gap-2">
            <CheckIcon state={check.state} />
            <div className="min-w-0">
              {href ? (
                <a href={href} className="font-medium underline-offset-4 hover:underline">
                  {check.label}
                </a>
              ) : (
                <span className="font-medium">{check.label}</span>
              )}
              <p className="text-xs text-muted-foreground">{check.detail}</p>
            </div>
          </li>
        )
      })}
    </ul>
  )
}
