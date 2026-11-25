import { afterEach, describe, expect, it } from 'vitest'

import { storedEvents, storeEvents } from './eventLog'
import type { LoggedEvent } from './eventLog'

const KEY = 'wata-admin-events'

afterEach(() => window.sessionStorage.clear())

describe('storedEvents', () => {
  it('keeps the events this tab stored', () => {
    const event: LoggedEvent = {
      id: 'a', at: '2026-11-25T09:00:00Z', href: '/positions', read: false, title: 'Position closed', tone: 'success',
    }
    storeEvents([event])
    expect(storedEvents()).toEqual([event])
  })

  it('reads the tone of an event kept before v0.102.0 from its variant', () => {
    // A tab open across the deploy keeps the events it was told of, in the shape of before.
    window.sessionStorage.setItem(KEY, JSON.stringify([
      { id: 'a', at: '2026-11-25T09:00:00Z', href: null, read: true, title: 'Backtest failed', variant: 'error' },
      { id: 'b', at: '2026-11-25T08:00:00Z', href: null, read: true, title: 'Restart', variant: 'default' },
      { id: 'c', at: '2026-11-25T07:00:00Z', href: null, read: true, title: 'Odd', variant: 'unheard-of' },
    ]))
    const events = storedEvents()
    expect(events.map((event) => event.tone)).toEqual(['danger', 'neutral', 'neutral'])
    expect(events.every((event) => !('variant' in event))).toBe(true)
  })

  it('gives nothing for what it cannot read', () => {
    window.sessionStorage.setItem(KEY, '{not json')
    expect(storedEvents()).toEqual([])
  })
})
