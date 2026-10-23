import { ArrowLeft, Home } from 'lucide-react'
import type { ReactNode } from 'react'
import { useLocation, useNavigate } from 'react-router'

import { ButtonLink } from '@/components/ButtonLink'
import type { Crumb } from '@/components/navigation'
import { usePageName, useTabTitle } from '@/components/page-trail-context'
import { RouterLink } from '@/components/RouterLink'
import { Button } from '@/components/ui/button'
import { NotFound } from '@/components/ui/not-found'
import type { NotFoundSuggestion } from '@/components/ui/not-found'

interface NotFoundStateProps {
  /** The heading. */
  title: string
  description: ReactNode
  /** What the trail of the top bar and the tab call the page: the heading, unless said otherwise. */
  name?: string
  /** The pages between its entry of the menu and itself, as PageHeader takes them (the run of a day). */
  parents?: Crumb[]
  /** Up to three pages that may be what was looked for. */
  suggestions?: NotFoundSuggestion[]
}

/**
 * What a page shows when its address names nothing: the router's catch-all, or
 * a page whose run, script, asset… the server does not have (its 404). EasyUI's
 * 404, named in the trail and the tab as any page is, with the way home and back.
 */
export function NotFoundState({ title, description, name = title, parents, suggestions }: NotFoundStateProps) {
  const navigate = useNavigate()
  // The router's address, not the window's: the same in a test under a memory router.
  const { pathname } = useLocation()
  usePageName(name, parents)
  useTabTitle(name)
  return (
    <NotFound title={title} description={description} path={pathname} suggestions={suggestions} LinkComponent={RouterLink}>
      {/* The dashboard is a place, a link; the page before is a step back in this tab's history. */}
      <ButtonLink to="/" leftIcon={<Home className="size-4" />}>
        Back to the dashboard
      </ButtonLink>
      <Button variant="outline" leftIcon={<ArrowLeft className="size-4" />} onClick={() => navigate(-1)}>
        Previous page
      </Button>
    </NotFound>
  )
}
