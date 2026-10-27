import { IoitiProvider, PageTrailProvider, TopBarTrail } from 'ioiti-ui'
import { RouterRoot } from 'ioiti-ui/react-router'
import type { ReactNode } from 'react'
import { useLocation } from 'react-router'

import { ADMIN_MENU, APP_NAME } from '@/components/navigation'

/**
 * A page under the trail of the top bar, as the shell shows it, without the
 * rest of the shell. Inside a router: the links are the router's and the tab
 * is named as the application names it (App.tsx). Both forms of the trail are
 * rendered (no CSS here): the whole one is the first navigation named "Breadcrumb".
 */
export function WithTrail({ children }: { children: ReactNode }) {
  const { pathname } = useLocation()
  return (
    <IoitiProvider appName={APP_NAME}>
      <RouterRoot>
        <PageTrailProvider menu={ADMIN_MENU} pathname={pathname}>
          <TopBarTrail />
          {children}
        </PageTrailProvider>
      </RouterRoot>
    </IoitiProvider>
  )
}
