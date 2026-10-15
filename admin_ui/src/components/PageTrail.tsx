import { ChevronLeft, ChevronRight, House } from 'lucide-react'
import { useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { Link, useLocation } from 'react-router'

import { RouterLink } from '@/components/RouterLink'
import { Breadcrumb } from '@/components/ui/breadcrumb'
import type { BreadcrumbItem } from '@/components/ui/breadcrumb'
import { cn } from '@/lib/utils'

import { NamedContext, PublisherContext, usePageTrail } from './page-trail-context'
import type { Named } from './page-trail-context'

const HOME: BreadcrumbItem = { label: 'Home', href: '/', icon: <House aria-hidden /> }

/**
 * Keeps the name the page shown gives itself (usePageName), for the trail of
 * the top bar. A name is kept with its address and only read for it: the page
 * left behind takes its name away after the next page is drawn, never before.
 */
export function PageTrailProvider({ children }: { children: ReactNode }) {
  const { pathname } = useLocation()
  const [named, publish] = useState<Named | null>(null)
  const publisher = useMemo(() => ({ pathname, publish }), [pathname])
  return (
    <PublisherContext.Provider value={publisher}>
      <NamedContext.Provider value={named?.pathname === pathname ? named : null}>{children}</NamedContext.Provider>
    </PublisherContext.Provider>
  )
}

/**
 * The trail in the top bar: whole on a wide screen. Narrower, beside the
 * sidebar, its last two steps. On a phone, where the top bar has room for a
 * word or two, the step before the page alone (a link back when it is one):
 * the page's own name is its heading, just under.
 */
export function TopBarTrail() {
  const { pathname } = useLocation()
  const items: BreadcrumbItem[] = [HOME, ...usePageTrail(pathname)]
  const tail = items.slice(-2)
  return (
    <>
      <Breadcrumb as={RouterLink} items={items} wrap={false} maxItems={items.length} className="hidden min-w-0 lg:block" />
      <nav aria-label="Breadcrumb" className="min-w-0 lg:hidden">
        <ol className="flex min-w-0 items-center gap-1 text-sm text-muted-foreground">
          {tail.map((item, index) => {
            const last = index === tail.length - 1
            return (
              <li
                key={`${index}-${item.label}`}
                className={cn(
                  'flex min-w-0 items-center gap-1',
                  !last && 'md:max-w-[45%] md:shrink-0',
                  last && tail.length > 1 && 'hidden md:flex',
                )}
              >
                {index > 0 && <ChevronRight aria-hidden className="size-3.5 shrink-0 text-neutral-300 dark:text-neutral-700" />}
                {last || !item.href ? (
                  <span aria-current={last ? 'page' : undefined} className={cn('truncate', last && 'font-medium text-foreground')}>
                    {item.label}
                  </span>
                ) : (
                  <Link to={item.href} className="flex min-w-0 items-center gap-0.5 hover:text-foreground">
                    <ChevronLeft aria-hidden className="size-4 shrink-0 md:hidden" />
                    <span className="truncate">{item.label}</span>
                  </Link>
                )}
              </li>
            )
          })}
        </ol>
      </nav>
    </>
  )
}
