import { createContext, useContext } from 'react'

import type { CloseAsked, SignalSent } from './follow'

export type Followed = ({ kind: 'close' } & CloseAsked) | ({ kind: 'signal' } & SignalSent)

export const FollowContext = createContext<((followed: Followed) => void) | null>(null)

/**
 * Follows a request to its outcome in the card the shell shows, one at a time: the card belongs to the shell, not
 * to the row it was asked from, which goes when its position closes. Null outside the shell (a page tested alone).
 */
export function useFollow(): ((followed: Followed) => void) | null {
  return useContext(FollowContext)
}
