import type { ComponentProps } from 'react'
import { Link } from 'react-router'

/**
 * React Router's link for a component that hands its link an `href` (EasyUI's
 * Breadcrumb): `Link` reads `to`, and without it every link would lead to the
 * page already shown.
 */
export function RouterLink({ href = '', ...props }: ComponentProps<'a'>) {
  return <Link to={href} {...props} />
}
