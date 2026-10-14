import { House } from 'lucide-react'
import { useEffect } from 'react'
import type { ReactNode } from 'react'

import { RouterLink } from '@/components/RouterLink'
import { Breadcrumb } from '@/components/ui/breadcrumb'
import type { BreadcrumbItem } from '@/components/ui/breadcrumb'
import { cn } from '@/lib/utils'

interface PanelProps {
  title?: ReactNode
  description?: ReactNode
  action?: ReactNode
  className?: string
  bodyClassName?: string
  children: ReactNode
}

/** The card every section of a page sits in. Same surface as the EasyUI data table. */
export function Panel({ title, description, action, className, bodyClassName, children }: PanelProps) {
  return (
    <section
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
   * Where the page is, for the pages deeper than the menu (an asset of the
   * Data section): from the dashboard, which is always first, to the page
   * itself, which is last and not a link.
   */
  breadcrumb?: BreadcrumbItem[]
}

const HOME: BreadcrumbItem = { label: 'Home', href: '/', icon: <House aria-hidden /> }

export function PageHeader({ title, description, actions, breadcrumb }: PageHeaderProps) {
  // The tab says which page it is: several are often open side by side.
  useEffect(() => {
    document.title = `${title} · WATA Admin`
    return () => {
      document.title = 'WATA Admin'
    }
  }, [title])

  const header = (
    <div className="flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0">
        <h1 className="text-2xl tracking-tight">{title}</h1>
        {description && <p className="mt-1 text-sm text-muted-foreground">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  )
  if (!breadcrumb) return header
  return (
    <div className="flex flex-col gap-1.5">
      {/* Its links are padded: drawn back to the edge the title starts at. */}
      <Breadcrumb as={RouterLink} items={[HOME, ...breadcrumb]} className="-ml-2" />
      {header}
    </div>
  )
}
