import { NotFoundState as PackageNotFoundState } from 'ioiti-ui'
import type { NotFoundStateProps } from 'ioiti-ui'
import { useLocation } from 'react-router'

import { DASHBOARD_HOME } from '@/components/navigation'

/**
 * What a page shows when its address names nothing: the router's catch-all, or
 * a page whose run, script, asset… the server does not have (its 404). ioiti-ui's,
 * with the way back to the dashboard and the router's address (the same in a
 * test under a memory router as in the browser).
 */
export function NotFoundState(props: NotFoundStateProps) {
  const { pathname } = useLocation()
  return <PackageNotFoundState home={DASHBOARD_HOME} pathname={pathname} {...props} />
}
