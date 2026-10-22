import { ArrowLeft, Home } from 'lucide-react'
import { useNavigate } from 'react-router'

import { ButtonLink } from '@/components/ButtonLink'
import { PAGE_NOT_FOUND } from '@/components/navigation'
import { usePageName } from '@/components/page-trail-context'
import { Button } from '@/components/ui/button'
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
      showCoordinates={false}
    >
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
