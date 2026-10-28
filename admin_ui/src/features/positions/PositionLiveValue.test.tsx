import { act, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { Position } from '@/lib/types'

import { PositionLiveValue } from './PositionLiveValue'

const OPEN = {
  position_id: '5003',
  action: 'long',
  instrument_name: 'TURBO LONG NASDAQ-100 KO 24150 SG',
  instrument_currency: 'EUR',
  position_amount: 260,
  position_open_price: 8.42,
  position_status: 'Open',
  execution_time_open: '2026-10-12T06:51:21Z',
  live_bid: 8.57,
  live_performance_percent: 1.78,
  live_observed_at: '2026-10-12T06:59:50Z',
} as Position

describe('the live value of an open position', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-10-12T07:00:00Z'))
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('waits for the monitor before it has seen a price', () => {
    render(<PositionLiveValue position={{ ...OPEN, live_performance_percent: null } as Position} />)
    expect(screen.getByText('waiting for the monitor')).toBeInTheDocument()
  })

  it('says the bid the monitor saw and how long ago, kept true as time goes by', () => {
    const { container } = render(<PositionLiveValue position={OPEN} timeZone="Europe/Paris" />)
    expect(container).toHaveTextContent(/bid €8\.57 · 10 s ago/)
    expect(screen.queryByText(/stale/)).not.toBeInTheDocument()

    act(() => {
      vi.advanceTimersByTime(5000)
    })
    expect(container).toHaveTextContent(/bid €8\.57 · 15 s ago/)
  })

  it('says each figure stale once the monitor has said nothing for a minute', () => {
    render(<PositionLiveValue position={OPEN} timeZone="Europe/Paris" />)
    act(() => {
      vi.advanceTimersByTime(61_000)
    })
    // The performance, what selling would bring, and the bid.
    expect(screen.getAllByText('(stale)', { exact: false })).toHaveLength(3)
  })

  it("never says the server's moment is still to come when its clock is ahead of the browser's", () => {
    const { container } = render(
      <PositionLiveValue position={{ ...OPEN, live_observed_at: '2026-10-12T07:00:02Z' } as Position} timeZone="Europe/Paris" />,
    )
    expect(container).toHaveTextContent(/· 0 s ago/)
  })
})
