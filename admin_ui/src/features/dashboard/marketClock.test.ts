import { describe, expect, it } from 'vitest'

import { cityOfZone, marketClock, usOpenAt } from './marketClock'

const PARIS = 'Europe/Paris'

describe('the US open, read in Paris', () => {
  it('is at 15:30, six hours apart, while both have their summer time', () => {
    expect(marketClock(Date.parse('2026-10-23T12:00:00Z'), PARIS)).toEqual({ openHere: '15:30', hoursApart: 6 })
  })

  it('is at 14:30, five hours apart, in the week Europe has changed its clocks and New York not yet', () => {
    expect(marketClock(Date.parse('2026-10-27T12:00:00Z'), PARIS)).toEqual({ openHere: '14:30', hoursApart: 5 })
  })

  it('is back at 15:30 once both have', () => {
    expect(marketClock(Date.parse('2026-11-03T12:00:00Z'), PARIS)).toEqual({ openHere: '15:30', hoursApart: 6 })
  })

  it('is at 14:30 in March, when New York changes first', () => {
    expect(marketClock(Date.parse('2026-03-10T12:00:00Z'), PARIS)).toEqual({ openHere: '14:30', hoursApart: 5 })
  })

  it('is on New York’s day, late in the evening in Paris too', () => {
    // 23:30 in Paris on the 23rd is still the 23rd in New York.
    expect(new Date(usOpenAt(Date.parse('2026-10-23T21:30:00Z'))).toISOString()).toBe('2026-10-23T13:30:00.000Z')
  })
})

describe('the city of a zone', () => {
  it('is the last part of its name', () => {
    expect(cityOfZone('Europe/Paris')).toBe('Paris')
    expect(cityOfZone('America/New_York')).toBe('New York')
    expect(cityOfZone('UTC')).toBe('UTC')
  })
})
