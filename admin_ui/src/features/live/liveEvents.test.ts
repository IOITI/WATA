import { describe, expect, it } from 'vitest'

import { describeEvent, LIVE_EVENT_TYPES, staleQueries } from './liveEvents'

describe('describeEvent', () => {
  it('reports a closed position with its result, good or bad', () => {
    const won = describeEvent({
      type: 'position_closed', instrument_name: 'TURBO LONG', profit_loss: 69.4, performance_percent: 2.6,
    })
    const lost = describeEvent({
      type: 'position_closed', instrument_name: 'TURBO LONG', profit_loss: -102.4, performance_percent: -9.5,
    })

    expect(won).toEqual({ title: 'Position closed', body: 'TURBO LONG · +€69.40 (+2.60%)', variant: 'success' })
    expect(lost?.body).toBe('TURBO LONG · −€102.40 (−9.50%)')
    expect(lost?.variant).toBe('warning')
  })

  it('does not invent a result that is not recorded yet', () => {
    const notice = describeEvent({ type: 'position_closed', instrument_name: 'TURBO LONG', profit_loss: null })

    expect(notice?.body).toBe('TURBO LONG · result not recorded yet')
    expect(notice?.variant).toBe('info')
  })

  it('says what was done with a signal and why', () => {
    const refused = describeEvent({
      type: 'signal_decided', action: 'long', outcome: 'refused', reason: 'Cooldown active',
    })
    const failed = describeEvent({ type: 'signal_decided', action: 'close-position', outcome: 'failed' })

    expect(refused).toEqual({ title: 'Long signal: refused', body: 'Cooldown active', variant: 'warning' })
    expect(failed?.title).toBe('Close all signal: failed')
    expect(failed?.variant).toBe('error')
  })

  it('distinguishes a service going down from one coming back', () => {
    const down = describeEvent({ type: 'service_status', name: 'Position monitor', status: 'down', detail: 'No consumer' })
    const up = describeEvent({ type: 'service_status', name: 'Position monitor', status: 'ok' })

    expect(down).toEqual({ title: 'Position monitor is down', body: 'No consumer', variant: 'error' })
    expect(up).toEqual({ title: 'Position monitor is back up', body: undefined, variant: 'success' })
  })

  it('stays silent for what the dashboard already shows, and for events it does not know', () => {
    expect(describeEvent({ type: 'entries_paused' })).toBeNull()
    expect(describeEvent({ type: 'entries_resumed' })).toBeNull()
    expect(describeEvent({ type: 'something_new' })).toBeNull()
  })
})

describe('staleQueries', () => {
  it('names the data each event makes out of date', () => {
    expect(staleQueries({ type: 'position_closed' })).toEqual(
      expect.arrayContaining(['overview', 'positions', 'performance', 'exits', 'report']),
    )
    expect(staleQueries({ type: 'entries_paused' })).toEqual(['overview'])
    expect(staleQueries({ type: 'service_status' })).toEqual(['health'])
    expect(staleQueries({ type: 'something_new' })).toEqual([])
  })

  it('covers every event type the page subscribes to', () => {
    for (const type of LIVE_EVENT_TYPES) expect(staleQueries({ type }).length).toBeGreaterThan(0)
  })
})
