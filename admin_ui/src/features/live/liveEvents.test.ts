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

  it("says Saxo refused to sell a position, how often since the last notice, and Saxo's answer", () => {
    const refused = describeEvent({
      type: 'order_refused', position_id: '7779123400', instrument_name: 'TURBO LONG', count: 5, outcome: 'refused',
      http_status: 400, error_code: 'OtherError', error_message: 'À ce stade, seuls des ordres limites sont autorisés',
    })
    const unanswered = describeEvent({ type: 'order_refused', position_id: '7779123400', count: 1, outcome: 'no_answer' })

    expect(refused).toEqual({
      title: 'Saxo refused a sell (×5)',
      body: 'TURBO LONG · position 7779123400 · “À ce stade, seuls des ordres limites sont autorisés” (OtherError, HTTP 400)',
      variant: 'error',
    })
    expect(unanswered).toEqual({ title: 'Saxo did not answer a sell', body: 'position 7779123400', variant: 'error' })
  })

  it('distinguishes a service going down from one coming back', () => {
    const down = describeEvent({ type: 'service_status', name: 'Position monitor', status: 'down', detail: 'No consumer' })
    const up = describeEvent({ type: 'service_status', name: 'Position monitor', status: 'ok' })

    expect(down).toEqual({ title: 'Position monitor is down', body: 'No consumer', variant: 'error' })
    expect(up).toEqual({ title: 'Position monitor is back up', body: undefined, variant: 'success' })
  })

  it('says when a backtest ended, and nothing when the operator stopped it', () => {
    expect(describeEvent({ type: 'backtest_finished', state: 'done', name: 'higher target' })).toEqual({
      title: 'Backtest finished', body: 'higher target', variant: 'success',
    })
    expect(describeEvent({ type: 'backtest_finished', state: 'failed', name: '' })).toEqual({
      title: 'Backtest failed', body: undefined, variant: 'error',
    })
    expect(describeEvent({ type: 'backtest_finished', state: 'cancelled' })).toBeNull()
  })

  it('says how a download of prices ended, and that days are usually still missing', () => {
    expect(describeEvent({ type: 'prices_fetch_finished', state: 'done' })).toEqual({
      title: 'Prices downloaded', body: undefined, variant: 'success',
    })
    expect(describeEvent({ type: 'prices_fetch_finished', state: 'incomplete' })).toEqual({
      title: 'The download of prices ended',
      body: 'Some days are still missing: run the same period again.',
      variant: 'warning',
    })
    expect(describeEvent({ type: 'prices_fetch_finished', state: 'stopped' })).toBeNull()
  })

  it('names the Dukascopy instrument downloaded when it is not the history kept up to date', () => {
    expect(describeEvent({ type: 'prices_fetch_finished', state: 'done', label: 'de40' })?.title).toBe(
      'Prices of de40 downloaded',
    )
    expect(describeEvent({ type: 'prices_fetch_finished', state: 'failed', label: 'de40' })?.title).toBe(
      'The download of prices of de40 ended',
    )
    expect(describeEvent({ type: 'prices_fetch_finished', state: 'done', label: 'us100' })?.title).toBe('Prices downloaded')
  })

  it('says nothing of the downloads the service makes by itself, night after night', () => {
    expect(describeEvent({ type: 'prices_fetch_finished', state: 'done', automatic: true })).toBeNull()
    expect(describeEvent({ type: 'prices_fetch_finished', state: 'incomplete', automatic: true })).toBeNull()
    expect(describeEvent({ type: 'prices_fetch_finished', state: 'done', automatic: false })).not.toBeNull()
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
    expect(staleQueries({ type: 'order_refused' })).toEqual(['overview', 'positions', 'position', 'orders'])
    expect(staleQueries({ type: 'service_status' })).toEqual(['health', 'data-providers', 'data-provider'])
    expect(staleQueries({ type: 'backtest_finished' })).toEqual(['backtests', 'backtest'])
    expect(staleQueries({ type: 'prices_fetch_finished' })).toEqual([
      'history-coverage', 'data-assets', 'data-asset', 'data-providers', 'data-provider',
    ])
    expect(staleQueries({ type: 'something_new' })).toEqual([])
  })

  it('covers every event type the page subscribes to', () => {
    for (const type of LIVE_EVENT_TYPES) expect(staleQueries({ type }).length).toBeGreaterThan(0)
  })
})
