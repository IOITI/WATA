import type { ReactNode } from 'react'

import { PageTrailProvider, TopBarTrail } from '@/components/PageTrail'

/**
 * A page under the trail of the top bar, as the shell shows it, without the
 * rest of the shell. Inside a router. Both forms of the trail are rendered (no
 * CSS here): the whole one is the first navigation named "Breadcrumb".
 */
export function WithTrail({ children }: { children: ReactNode }) {
  return (
    <PageTrailProvider>
      <TopBarTrail />
      {children}
    </PageTrailProvider>
  )
}
