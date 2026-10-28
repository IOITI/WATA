import { useQuery } from '@tanstack/react-query'
import { cn, DynamicIsland, SignedValue, toneOf, useNow } from 'ioiti-ui'
import type { DynamicIslandState } from 'ioiti-ui'
import { useState, type ReactNode } from 'react'
import { Link } from 'react-router'

import { ActionBadge } from '@/components/status'
import { useSession } from '@/features/auth/auth-context'
import { ClosePositionButton } from '@/features/positions/ClosePositionButton'
import { PositionLiveValue } from '@/features/positions/PositionLiveValue'
import { unrealisedProfitLoss } from '@/features/positions/unrealised'
import { api } from '@/lib/api'

import { entryBlockers } from './entryState'

const DOT = {
  positive: 'bg-positive',
  negative: 'bg-negative',
  warning: 'bg-warning',
  neutral: 'bg-chart-muted',
}

function Dot({ tone }: { tone: keyof typeof DOT }) {
  return <span aria-hidden className={cn('size-2 shrink-0 rounded-full', DOT[tone])} />
}

/**
 * On every page, in the top bar: the open positions' live result, or whether a new entry would be taken. Opened,
 * the positions with their Close, and what holds the next entry back. Reads the shell's overview, which is read
 * every few seconds while a position is open.
 */
export function LivePill() {
  const { actions_enabled: actionsEnabled, timezone } = useSession()
  const [state, setState] = useState<DynamicIslandState>('collapsed')
  const expanded = state === 'expanded'
  // What depends on the time: an entry window opening or closing.
  const now = useNow(expanded ? 1_000 : 15_000)
  const overviewQuery = useQuery({ queryKey: ['overview'], queryFn: api.overview })
  const overview = overviewQuery.data
  if (!overview) return null

  const positions = overview.open_positions ?? []
  const blockers = entryBlockers(overview, now)
  const paused = overview.entries_paused_since !== null

  let compact: ReactNode
  let quiet = false
  if (positions.length > 0) {
    // One figure: in euros when each position's is known, else the one position's percent.
    const unrealised = positions.map(unrealisedProfitLoss)
    const total = unrealised.every((value) => value !== null) ? unrealised.reduce<number>((sum, value) => sum + (value ?? 0), 0) : null
    const single = positions.length === 1 ? (positions[0].live_performance_percent ?? null) : null
    compact = (
      <>
        <Dot tone={toneOf(total ?? single)} />
        <span className="hidden sm:inline">{positions.length} open</span>
        {total !== null ? (
          <SignedValue value={total} kind="money" animate />
        ) : (
          single !== null && <SignedValue value={single} kind="percent" animate />
        )}
      </>
    )
  } else if (paused) {
    compact = (
      <>
        <Dot tone="warning" />
        <span>Entries paused</span>
      </>
    )
  } else if (blockers.length > 0) {
    compact = (
      <>
        <Dot tone="neutral" />
        <span className="hidden sm:inline">Entries blocked</span>
        <span className="sm:hidden">Blocked</span>
      </>
    )
  } else {
    quiet = true
    compact = (
      <>
        <Dot tone="neutral" />
        <span className="text-muted-foreground">No position</span>
      </>
    )
  }

  return (
    <DynamicIsland
      label="Live trading"
      state={state}
      onStateChange={setState}
      compact={compact}
      // Nothing to say on a phone, where the top bar has no room to spare.
      className={quiet ? 'hidden shrink-0 sm:inline-flex' : 'shrink-0'}
    >
      <div className="space-y-3 pr-7 text-xs">
        <p className="text-sm font-semibold">
          {positions.length === 0 ? 'No open position' : `${positions.length} open position${positions.length === 1 ? '' : 's'}`}
        </p>
        {positions.map((position) => (
          <div key={position.position_id} className="space-y-1.5">
            <div className="flex items-start justify-between gap-2">
              <p className="min-w-0 truncate font-medium">{position.instrument_name ?? `Position ${position.position_id}`}</p>
              <ActionBadge action={position.action} />
            </div>
            <div className="flex items-end justify-between gap-2">
              <PositionLiveValue position={position} timeZone={timezone} />
              {actionsEnabled && <ClosePositionButton position={position} />}
            </div>
          </div>
        ))}
        <div className="space-y-1 border-t border-border/60 pt-2">
          <p className="font-medium">
            Next entry: {blockers.length > 0 ? <span className="text-warning">blocked</span> : <span className="text-positive">open</span>}
          </p>
          {blockers.length > 0 && (
            <ul className="space-y-0.5 text-muted-foreground">
              {blockers.map((blocker) => (
                <li key={blocker.code}>{blocker.reason}</li>
              ))}
            </ul>
          )}
        </div>
        <Link to="/" onClick={() => setState('collapsed')} className="inline-block font-medium underline-offset-4 hover:underline">
          {paused ? 'Resume entries on the dashboard' : 'The dashboard'}
        </Link>
      </div>
    </DynamicIsland>
  )
}
