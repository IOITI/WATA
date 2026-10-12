import { useNavigate } from 'react-router'

import { NotFound } from '@/components/ui/not-found'

export function NotFoundPage() {
  const navigate = useNavigate()
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
