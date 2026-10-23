import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { Position } from '@/lib/types'

import { forgetLiveSamples, KEEP_MS, liveSamples, onLiveSample, recordLiveValues } from './liveSamples'

const NOW = Date.parse('2026-10-23T14:00:00Z')

function open(id: string, performance: number | null, observedAt: string | null): Position {
  return { position_id: id, live_performance_percent: performance, live_observed_at: observedAt } as Position
}

beforeEach(() => {
  sessionStorage.clear()
  forgetLiveSamples()
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('the live values a tab keeps', () => {
  it('keeps one sample per moment the monitor saw a position', () => {
    recordLiveValues([open('1', 0.4, '2026-10-23T13:59:50Z')], NOW)
    recordLiveValues([open('1', 0.4, '2026-10-23T13:59:50Z')], NOW)
    recordLiveValues([open('1', 0.6, '2026-10-23T13:59:55Z')], NOW)

    expect(liveSamples('1')).toEqual([
      { t: Date.parse('2026-10-23T13:59:50Z'), v: 0.4 },
      { t: Date.parse('2026-10-23T13:59:55Z'), v: 0.6 },
    ])
  })

  it('drops what is older than half an hour, and the positions no longer open', () => {
    recordLiveValues([open('1', 0.4, new Date(NOW - KEEP_MS - 1).toISOString()), open('2', 1, '2026-10-23T13:59:00Z')], NOW)
    recordLiveValues([open('1', 0.5, '2026-10-23T13:59:58Z')], NOW)

    expect(liveSamples('1')).toEqual([{ t: Date.parse('2026-10-23T13:59:58Z'), v: 0.5 }])
    expect(liveSamples('2')).toEqual([])
  })

  it('leaves out a position the monitor has not valued yet', () => {
    recordLiveValues([open('1', null, null)], NOW)

    expect(liveSamples('1')).toEqual([])
  })

  it('hands each new sample to the charts that listen', () => {
    const heard = vi.fn()
    const stop = onLiveSample(heard)
    recordLiveValues([open('1', 0.4, '2026-10-23T13:59:50Z')], NOW)
    recordLiveValues([open('1', 0.4, '2026-10-23T13:59:50Z')], NOW)
    stop()
    recordLiveValues([open('1', 0.7, '2026-10-23T13:59:59Z')], NOW)

    expect(heard).toHaveBeenCalledTimes(1)
    expect(heard).toHaveBeenCalledWith('1', { t: Date.parse('2026-10-23T13:59:50Z'), v: 0.4 })
  })

  it('finds them again after a reload of the page', () => {
    recordLiveValues([open('1', 0.4, '2026-10-23T13:59:50Z')], NOW)
    forgetLiveSamples()

    expect(liveSamples('1')).toHaveLength(1)
  })

  it('still keeps them for the page where the storage refuses them', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('QuotaExceededError')
    })
    recordLiveValues([open('1', 0.4, '2026-10-23T13:59:50Z')], NOW)

    expect(liveSamples('1')).toHaveLength(1)
  })
})
