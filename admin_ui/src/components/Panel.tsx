import type { ReactNode } from 'react'

import type { Crumb } from '@/components/navigation'
import { usePageName, useTabTitle } from '@/components/page-trail-context'
import { cn } from '@/lib/utils'

interface PanelProps {
  /** An anchor a link can lead to (`#trades`). */
  id?: string
  title?: ReactNode
  description?: ReactNode
  action?: ReactNode
  className?: string
  bodyClassName?: string
  children: ReactNode
}

/** The card every section of a page sits in. Same surface as the EasyUI data table. */
export function Panel({ id, title, description, action, className, bodyClassName, children }: PanelProps) {
  return (
    <section
      id={id}
      className={cn(
        'rounded-2xl border border-border/70 bg-surface/50 text-foreground dark:bg-surface/25',
        className,
      )}
    >
      {(title || action) && (
        <header className="flex flex-wrap items-start justify-between gap-3 px-5 pt-5">
          <div className="min-w-0">
            {title && <h2 className="text-sm font-semibold">{title}</h2>}
            {description && <p className="mt-1 text-xs text-muted-foreground">{description}</p>}
          </div>
          {action && <div className="flex shrink-0 items-center gap-2">{action}</div>}
        </header>
      )}
      <div className={cn('p-5', bodyClassName)}>{children}</div>
    </section>
  )
}

interface PageHeaderProps {
  title: string
  description?: ReactNode
  actions?: ReactNode
  /**
   * For the trail of the top bar, on a page deeper than the menu: the pages
   * between its entry of the menu and itself (the run a day of a backtest
   * belongs to). The title is the page's own step.
   */
  parents?: Crumb[]
}

/**
 * The title of a page and what it does. On a page deeper than the menu, its
 * title also ends the trail of the top bar, which every page has.
 */
export function PageHeader({ title, description, actions, parents }: PageHeaderProps) {
  usePageName(title, parents)
  useTabTitle(title)

  return (
    <div className="flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0">
        <h1 className="text-2xl tracking-tight">{title}</h1>
        {description && <p className="mt-1 text-sm text-muted-foreground">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  )
}
