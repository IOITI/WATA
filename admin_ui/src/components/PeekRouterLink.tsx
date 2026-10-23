import type { ComponentProps, ReactNode } from 'react'

import { RouterLink } from '@/components/RouterLink'
import { PeekCard } from '@/components/ui/peek-card'
import { RunPeek } from '@/features/backtests/RunPeek'
import { PositionPeek } from '@/features/positions/PositionPeek'

const RUN = /^\/backtests\/(\d{8}-\d{6}-[0-9a-f]{6})$/
const POSITION = /^\/positions\?find=([^&]+)$/

/** What the card of a link shows, from where it leads: a run, a position, or nothing to preview. */
function previewOf(href: string): (() => ReactNode) | null {
  const run = RUN.exec(href)
  if (run) return () => <RunPeek id={run[1]} />
  const position = POSITION.exec(href)
  if (position) return () => <PositionPeek id={decodeURIComponent(position[1])} />
  return null
}

function Previewed({ openOnTap, ...props }: ComponentProps<'a'> & { openOnTap: boolean }) {
  const preview = previewOf(props.href ?? '')
  if (!preview) return <RouterLink {...props} />
  return (
    <PeekCard renderContent={preview} href={props.href} linkComponent={RouterLink} openOnTap={openOnTap}>
      <RouterLink {...props} />
    </PeekCard>
  )
}

/** React Router's link for a copied component, a run's or a position's previewed (`PeekCard`). */
export function PeekRouterLink(props: ComponentProps<'a'>) {
  return <Previewed {...props} openOnTap />
}

/** The same, previewed for a pointer and the keyboard only: a tap follows it at once (the bell's entries). */
export function HoverPeekRouterLink(props: ComponentProps<'a'>) {
  return <Previewed {...props} openOnTap={false} />
}
