import { describe, expect, it } from 'vitest'

import { sourceNotes } from './signalSources'

describe('sourceNotes', () => {
  it('says nothing while every index takes TradingView’s alerts, or for a web server of before', () => {
    expect(sourceNotes({ sources: {}, ignored: {} }, 'Europe/Paris')).toEqual([])
    expect(sourceNotes({ sources: { us100: 'tradingview' }, ignored: {} }, 'Europe/Paris')).toEqual([])
    expect(sourceNotes(null, 'Europe/Paris')).toEqual([])
  })

  it('says an index taken by the engine, and the alerts of TradingView it still receives', () => {
    const [quiet] = sourceNotes({ sources: { us100: 'engine' }, ignored: {} }, 'UTC')
    const [heard] = sourceNotes(
      {
        sources: { us100: 'engine' },
        ignored: { us100: { count: 3, first_at: '2026-10-27T07:00:00.000Z', last_at: '2026-10-27T09:31:00.000Z', last_action: 'long' } },
      },
      'UTC',
    )

    expect(quiet.text).toContain('takes its signals from the TA engine’s live rule alone')
    expect(quiet.text).toContain('No alert of TradingView has come for it')
    expect(heard.indice).toBe('us100')
    expect(heard.text).toContain('3 alerts of TradingView ignored')
    expect(heard.text).toContain('(long): switch it off in TradingView.')
  })
})
