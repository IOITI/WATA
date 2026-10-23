import { useQuery } from '@tanstack/react-query'
import { AnimatePresence, motion } from 'framer-motion'
import { useEffect, useState, type ReactNode } from 'react'

import { RouterLink } from '@/components/RouterLink'
import { PaymentStatus } from '@/components/ui/payment-status'
import { useSession } from '@/features/auth/auth-context'
import { api } from '@/lib/api'

import { closeProgress, FOLLOW_FOR_MS, POLL_MS, signalProgress, type CloseAsked, type FollowState, type SignalSent } from './follow'
import { FollowContext, type Followed } from './follow-context'

function followedKey(followed: Followed): string {
  return followed.kind === 'close' ? `close-${followed.positionId}-${followed.askedAt}` : `signal-${followed.signalUuid}`
}

/** The browser's clock, a second at a time while `ticking`: a step that waits too long says so without new data. */
function useTicking(ticking: boolean, setNow: (now: number) => void): void {
  useEffect(() => {
    if (!ticking) return
    const timer = setInterval(() => setNow(Date.now()), 1_000)
    return () => clearInterval(timer)
  }, [ticking, setNow])
}

/** Reads again every `POLL_MS` while `active`: the fallback of the live events, which refresh the queries at once. */
function useRefetching(refetch: () => unknown, active: boolean): void {
  useEffect(() => {
    if (!active) return
    const timer = setInterval(() => void refetch(), POLL_MS)
    return () => clearInterval(timer)
  }, [refetch, active])
}

/** Asking again until the card has its outcome, and never longer than `FOLLOW_FOR_MS`. */
function watching(state: FollowState, since: number, now: number): boolean {
  return !state.final && now - since < FOLLOW_FOR_MS
}

function Card({ state, onDismiss }: { state: FollowState; onDismiss: () => void }) {
  return (
    <PaymentStatus
      status={state.status}
      title={state.title}
      description={state.description}
      subject={state.subject}
      figures={state.figures}
      reference={state.reference}
      link={state.link}
      linkComponent={RouterLink}
      onDismiss={onDismiss}
    />
  )
}

function CloseCard({ asked, onDismiss }: { asked: CloseAsked; onDismiss: () => void }) {
  const { timezone } = useSession()
  const [now, setNow] = useState(() => Date.now())
  // The live events (a position closed, a sell refused) refresh this query at once; the interval is the fallback.
  const detailQuery = useQuery({ queryKey: ['position', asked.positionId], queryFn: () => api.position(asked.positionId) })
  const state = closeProgress(asked, detailQuery.data, now, timezone)
  const active = watching(state, asked.askedAt, now)
  useTicking(active, setNow)
  useRefetching(detailQuery.refetch, active)
  return <Card state={state} onDismiss={onDismiss} />
}

function SignalCard({ sent, onDismiss }: { sent: SignalSent; onDismiss: () => void }) {
  const { timezone } = useSession()
  const [now, setNow] = useState(() => Date.now())
  // The journal's own query (the Signals page's), so that its table follows too.
  const journalQuery = useQuery({ queryKey: ['journal'], queryFn: () => api.journal() })
  const entry = journalQuery.data?.items.find((item) => item.signal_uuid === sent.signalUuid)
  const bought = entry?.outcome === 'executed' ? entry.position_id : null
  const positionQuery = useQuery({ queryKey: ['position', bought], queryFn: () => api.position(bought ?? ''), enabled: !!bought })
  const state = signalProgress(sent, entry, positionQuery.data?.position, now, timezone)
  const active = watching(state, sent.sentAt, now)
  useTicking(active, setNow)
  useRefetching(bought ? positionQuery.refetch : journalQuery.refetch, active)
  return <Card state={state} onDismiss={onDismiss} />
}

/**
 * Follows what the operator asked for until it is done: a close until the position is sold (or Saxo refuses), a
 * signal sent by hand until the trader decides. One card at a time, docked under the top bar (across a phone, at the
 * right elsewhere), clear of the toasts and of a table's selection at the bottom, under the dialogs; a new request
 * replaces the one followed.
 */
export function FollowProvider({ children }: { children: ReactNode }) {
  const [followed, setFollowed] = useState<Followed | null>(null)
  const dismiss = () => setFollowed(null)

  return (
    <FollowContext.Provider value={setFollowed}>
      {children}
      <AnimatePresence>
        {followed && (
          <motion.div
            key={followedKey(followed)}
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 16 }}
            transition={{ type: 'spring', stiffness: 380, damping: 32 }}
            className="fixed inset-x-4 top-16 z-40 sm:inset-x-auto sm:right-6 sm:w-96"
          >
            {followed.kind === 'close' ? (
              <CloseCard asked={followed} onDismiss={dismiss} />
            ) : (
              <SignalCard sent={followed} onDismiss={dismiss} />
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </FollowContext.Provider>
  )
}
