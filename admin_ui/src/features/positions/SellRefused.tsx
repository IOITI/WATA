import { Hint } from 'ioiti-ui'
import { Link } from 'react-router'

import type { SellRefusals } from '@/lib/types'

import { orderLink } from './orderLife'
import { sellRefusalMarker, sellRefusalSentence } from './orderRequests'

/** Under an open position's name, while Saxo does not take its sells: how often, when last, and a way to them. */
export function SellRefused({
  positionId,
  refusals,
  timeZone,
}: {
  positionId: string
  refusals: SellRefusals
  timeZone: string
}) {
  const sentence = sellRefusalSentence(refusals, timeZone, true)
  // Named by the marker, described by the whole sentence: on hover, focus and a tap.
  return (
    <Hint content={sentence} className="mt-0.5 flex w-fit">
      <Link
        to={orderLink(positionId)}
        className="block w-fit text-xs font-medium text-negative underline decoration-negative/40 underline-offset-2 hover:decoration-negative"
      >
        {sellRefusalMarker(refusals, timeZone)}
      </Link>
    </Hint>
  )
}
