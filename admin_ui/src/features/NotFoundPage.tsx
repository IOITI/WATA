import { NotFoundPage as PackageNotFoundPage } from 'ioiti-ui'
import { useLocation } from 'react-router'

import { DASHBOARD_HOME } from '@/components/navigation'

/** The router's catch-all: an address under an entry of the menu that leads nowhere suggests that entry. */
export function NotFoundPage() {
  const { pathname } = useLocation()
  return (
    <PackageNotFoundPage
      description="The address does not match any page of the admin."
      home={DASHBOARD_HOME}
      pathname={pathname}
    />
  )
}
