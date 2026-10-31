import { Badge } from 'ioiti-ui'
import { Link } from 'react-router'

import type { LuxAlgoFamily, LuxAlgoIndicatorLine } from '@/lib/types'

import { LuxAlgoPicture } from './LuxAlgoPicture'
import { indicatorPath } from './luxalgoModel'
import { familyName } from './useLuxAlgo'

/**
 * An indicator of LuxAlgo's Library as a card: LuxAlgo's picture of it and its name, both leading to its page, and
 * what WATA knows of it as badges.
 */
export function IndicatorCard({ indicator, families, badges = [] }: {
  indicator: LuxAlgoIndicatorLine
  families?: LuxAlgoFamily[]
  badges?: { label: string; tone: 'success' | 'warning' | 'danger' | 'neutral' }[]
}) {
  return (
    <li className="glass-card flex flex-col overflow-hidden rounded-xl border border-border bg-surface">
      {/* The name below is the card's link for the keyboard and screen readers: the picture's is for the pointer. */}
      <Link to={indicatorPath(indicator.slug)} tabIndex={-1} aria-hidden className="block border-b border-border">
        <LuxAlgoPicture url={indicator.image_url} name={indicator.name} family={indicator.family} />
      </Link>
      <div className="flex flex-col gap-1.5 p-4">
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
      </div>
    </li>
  )
}
