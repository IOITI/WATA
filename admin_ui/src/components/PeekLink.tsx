import type { ReactNode } from 'react'
import { Link } from 'react-router'

import { RouterLink } from '@/components/RouterLink'
import { PeekCard } from '@/components/ui/peek-card'

interface PeekLinkProps {
  to: string
  /** What the card shows, read once it opens. */
  content: () => ReactNode
  className?: string
  children: ReactNode
  /** False: a tap follows the link at once, the card being for a pointer and the keyboard only. */
  openOnTap?: boolean
  'aria-label'?: string
}

/**
 * A link whose page is previewed on hover, on keyboard focus and on a first tap: the run, the position it leads to,
 * without leaving the page. It stays a link, which a click (and a second tap) follows.
 */
export function PeekLink({ to, content, className, children, openOnTap = true, 'aria-label': label }: PeekLinkProps) {
  return (
    <PeekCard renderContent={content} href={to} linkComponent={RouterLink} openOnTap={openOnTap}>
      <Link to={to} className={className} aria-label={label}>
        {children}
      </Link>
    </PeekCard>
  )
}

