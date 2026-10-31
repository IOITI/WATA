import { Badge } from 'ioiti-ui'
import { Link } from 'react-router'

import type { LuxAlgoFamily, LuxAlgoIndicatorLine } from '@/lib/types'

import { indicatorPath } from './luxalgoModel'
import { familyName } from './useLuxAlgo'

/** An indicator of LuxAlgo's Library as a card: a link to its page, with what WATA knows of it as badges. */
export function IndicatorCard({ indicator, families, badges = [] }: {
  indicator: LuxAlgoIndicatorLine
  families?: LuxAlgoFamily[]
  badges?: { label: string; tone: 'success' | 'warning' | 'danger' | 'neutral' }[]
}) {
  return (
    <li className="glass-card flex flex-col gap-1.5 rounded-xl border border-border bg-surface p-4">
      <Link to={indicatorPath(indicator.slug)} className="font-medium underline-offset-4 hover:underline">
        {indicator.name}
      </Link>
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        {indicator.family && <Badge variant="outline">{familyName(families, indicator.family)}</Badge>}
        {indicator.date_displayed && <span>{indicator.date_displayed}</span>}
        {badges.map((badge) => (
          <Badge key={badge.label} tone={badge.tone} dot>
            {badge.label}
          </Badge>
        ))}
      </div>
      {indicator.description && <p className="line-clamp-3 text-sm text-text-secondary">{indicator.description}</p>}
    </li>
  )
}
