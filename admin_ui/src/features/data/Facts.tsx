import { cn } from 'ioiti-ui'

import type { Fact } from '@/features/prices/availability'

/** What is known of an asset, label beside value; a value that says something is wrong in the warning colour. */
export function Facts({ facts, id }: { facts: Fact[]; id?: string }) {
  return (
    <dl id={id} className="space-y-1.5 text-xs">
      {facts.map((fact) => (
        <div key={fact.label} className="flex gap-2">
          <dt className="w-[5.75rem] shrink-0 text-muted-foreground">{fact.label}</dt>
          <dd className={cn('min-w-0 tabular-nums', fact.warning && 'text-warning')}>{fact.value}</dd>
        </div>
      ))}
    </dl>
  )
}
