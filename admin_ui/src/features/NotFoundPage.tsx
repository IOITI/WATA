import { useLocation } from 'react-router'

import { menuEntry, PAGE_NOT_FOUND } from '@/components/navigation'
import { NotFoundState } from '@/components/NotFoundState'

export function NotFoundPage() {
  const { pathname } = useLocation()
  // An address under an entry of the menu that leads nowhere (an old link, a typo): that entry may be what was meant.
  const entry = menuEntry(pathname)
  return (
    <NotFoundState
      title="There is no page here."
      description="The address does not match any page of the admin."
      name={PAGE_NOT_FOUND}
      suggestions={entry && entry.path !== '/' ? [{ label: entry.label, href: entry.path, description: entry.section }] : undefined}
    />
  )
}
