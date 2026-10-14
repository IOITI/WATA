import { useNavigate } from 'react-router'

import { PAGE_NOT_FOUND } from '@/components/navigation'
import { usePageName } from '@/components/page-trail-context'
import { NotFound } from '@/components/ui/not-found'

export function NotFoundPage() {
  const navigate = useNavigate()
  // No PageHeader here: the trail of the top bar is told directly.
  usePageName(PAGE_NOT_FOUND)
  return (
    <NotFound
      badgeLabel="404"
      title="There is no page here."
      description="The address does not match any page of the admin."
      actionLabel="Back to the dashboard"
      onAction={() => navigate('/')}
      secondaryActionLabel="Previous page"
      onSecondaryAction={() => navigate(-1)}
      showCoordinates={false}
    />
  )
}
