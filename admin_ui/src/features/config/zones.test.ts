import { describe, expect, it } from 'vitest'

import { clockIn, dayIn, gapBetween, instantOf, isZone, offsetMinutes, sameMoment, zoneLabel } from './zones'

describe('offsets', () => {
  it('follows each city through its change of the clocks', () => {
    expect(offsetMinutes('Europe/Paris', new Date('2026-01-15T12:00:00Z'))).toBe(60)
    expect(offsetMinutes('Europe/Paris', new Date('2026-07-15T12:00:00Z'))).toBe(120)
    expect(offsetMinutes('America/New_York', new Date('2026-01-15T12:00:00Z'))).toBe(-300)
    expect(offsetMinutes('America/New_York', new Date('2026-07-15T12:00:00Z'))).toBe(-240)
    expect(offsetMinutes('UTC', new Date('2026-07-15T12:00:00Z'))).toBe(0)
  })
})

describe('the same moment elsewhere', () => {
  it('finds the instant of a wall-clock time, on either side of a change of the clocks', () => {
    // Europe went back to winter time on 25 October 2026, the US a week later.
    expect(instantOf('2026-10-26', 2, 0, 'America/New_York').toISOString()).toBe('2026-10-26T06:00:00.000Z')
    expect(instantOf('2026-11-02', 2, 0, 'America/New_York').toISOString()).toBe('2026-11-02T07:00:00.000Z')
    expect(instantOf('2026-07-01', 9, 30, 'Europe/Paris').toISOString()).toBe('2026-07-01T07:30:00.000Z')
  })

  it('says an hour of New York as it is in Paris and in UTC that day', () => {
    // The week the clocks are changed apart: 02:00 in New York is 07:00 in Paris, an hour before the turbos are quoted.
    const apart = sameMoment(2, 0, 'America/New_York', ['Europe/Paris', 'UTC'], new Date('2026-10-26T12:00:00Z'))
    const usual = sameMoment(2, 0, 'America/New_York', ['Europe/Paris', 'UTC'], new Date('2026-11-09T12:00:00Z'))

    expect(apart).toEqual([
      { zone: 'Europe/Paris', clock: '07:00', dayShift: 0 },
      { zone: 'UTC', clock: '06:00', dayShift: 0 },
    ])
    expect(usual[0]).toEqual({ zone: 'Europe/Paris', clock: '08:00', dayShift: 0 })
  })

  it('says when the moment falls on the next day there', () => {
    const late = sameMoment(22, 0, 'America/New_York', ['Europe/Paris'], new Date('2026-11-09T12:00:00Z'))

    expect(late).toEqual([{ zone: 'Europe/Paris', clock: '04:00', dayShift: 1 }])
  })

  it('leaves out the zone the time is written in', () => {
    expect(sameMoment(9, 0, 'Europe/Paris', ['Europe/Paris', 'UTC'], new Date('2026-07-01T12:00:00Z'))).toEqual([
      { zone: 'UTC', clock: '07:00', dayShift: 0 },
    ])
  })
})

describe('the gap between two cities', () => {
  it('tells the weeks the clocks are changed apart from the rest of the year', () => {
    expect(gapBetween('Europe/Paris', 'America/New_York', new Date('2026-10-26T12:00:00Z'))).toEqual({ usual: 360, now: 300 })
    expect(gapBetween('Europe/Paris', 'America/New_York', new Date('2026-12-01T12:00:00Z'))).toEqual({ usual: 360, now: 360 })
  })
})

describe('names', () => {
  it('names a zone as a person would', () => {
    expect(zoneLabel('America/New_York')).toBe('New York')
    expect(zoneLabel('Europe/Paris')).toBe('Paris')
    expect(zoneLabel('UTC')).toBe('UTC')
  })

  it('knows which zones exist', () => {
    expect(isZone('America/New_York')).toBe(true)
    expect(isZone('Mars/Olympus')).toBe(false)
  })

  it('writes days and clocks in the zone asked', () => {
    const instant = new Date('2026-10-26T23:30:00Z')
    expect(dayIn(instant, 'Europe/Paris')).toBe('2026-10-27')
    expect(clockIn(instant, 'Europe/Paris')).toBe('00:30')
  })
})
