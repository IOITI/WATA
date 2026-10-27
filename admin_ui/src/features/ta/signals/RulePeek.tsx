import { useQuery } from '@tanstack/react-query'
import { PeekCard, Skeleton } from 'ioiti-ui'
import { RouterLink } from 'ioiti-ui/react-router'
import type { ReactNode } from 'react'

import { api } from '@/lib/api'

import { barsLabel, MODE_LABEL } from './ruleModel'

/** What a signal rule is: its mode, its script and version, what it runs on. */
export function RulePeek({ id }: { id: string }) {
  const rulesQuery = useQuery({ queryKey: ['ta-rules'], queryFn: api.taRules })
  if (rulesQuery.isPending) return <Skeleton className="h-16 w-full" />
  const rule = rulesQuery.data?.rules.find((item) => item.id === id)
  if (!rule) return <p>This rule is no longer among the signal rules.</p>
  const figures: [string, ReactNode][] = [
    ['Mode', MODE_LABEL[rule.mode]],
    ['Script', `${rule.script_name ?? rule.script.id} v${rule.script.version}`],
    ['Runs on', `${rule.asset.key}, ${barsLabel(rule.bars)}`],
    ['Indice', rule.indice],
  ]
  return (
    <div className="space-y-2.5">
      <p className="truncate text-sm font-semibold text-foreground">{rule.name}</p>
      <dl className="space-y-1">
        {figures.map(([label, value]) => (
          <div key={label} className="flex justify-between gap-3">
            <dt>{label}</dt>
            <dd className="min-w-0 truncate text-right text-foreground">{value}</dd>
          </div>
        ))}
      </dl>
    </div>
  )
}

/** A rule's name, previewed on hover, keyboard focus or a tap: rules have no page of their own. */
export function RulePeekName({ id, name }: { id: string; name: string }) {
  return (
    <PeekCard renderContent={() => <RulePeek id={id} />} href="/ta/signals" hrefLabel="The signal rules" linkComponent={RouterLink}>
      <button type="button" className="underline decoration-dotted underline-offset-4">
        {name}
      </button>
    </PeekCard>
  )
}
