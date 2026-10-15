import { describe, expect, it } from 'vitest'

import { lastDays, rangeParams } from './dateRange'
import {
  formatDateTime,
  formatDay,
  formatDuration,
  formatSpan,
  formatMoney,
  formatMoneyCompact,
  formatPercent,
  formatRelative,
  formatWallTime,
  toIsoDay,
  toneOf,
} from './format'

describe('formatMoney', () => {
  it('shows losses with a real minus sign and profits with a plus when signed', () => {
    expect(formatMoney(1234.5, 'EUR', true)).toBe('+€1,234.50')
    expect(formatMoney(-102.4, 'EUR', true)).toBe('−€102.40')
    expect(formatMoney(0, 'EUR', true)).toBe('€0.00')
  })

  it('does not add a plus to plain amounts', () => {
    expect(formatMoney(8.42)).toBe('€8.42')
    expect(formatMoney(-8.42)).toBe('−€8.42')
  })

  it('shows a dash for a missing or non-finite amount', () => {
    expect(formatMoney(null)).toBe('—')
    expect(formatMoney(undefined)).toBe('—')
    expect(formatMoney(Number.NaN)).toBe('—')
  })

  it('falls back to euros when the instrument has no currency', () => {
    expect(formatMoney(5, null)).toBe('€5.00')
  })

  it('compacts axis values', () => {
    expect(formatMoneyCompact(1800)).toBe('1.8k')
    expect(formatMoneyCompact(2_400_000)).toBe('2.4m')
    expect(formatMoneyCompact(-600)).toBe('−600')
  })
})

describe('formatPercent', () => {
  it('signs performance figures', () => {
    expect(formatPercent(2.6, true)).toBe('+2.60%')
    expect(formatPercent(-10.39, true)).toBe('−10.39%')
    expect(formatPercent(60.526, false, 1)).toBe('60.5%')
    expect(formatPercent(null)).toBe('—')
  })
})

describe('dates', () => {
  it('shows an instant in the trading timezone, not the browser one', () => {
    const today = new Date('2026-10-12T10:00:00Z')
    expect(formatDateTime('2026-10-11T20:52:12Z', 'Europe/Paris', true, today)).toBe('11 Oct, 22:52:12')
    expect(formatDateTime('2026-01-11T20:52:12Z', 'Europe/Paris', false, today)).toBe('11 Jan, 21:52')
    expect(formatDateTime(null, 'Europe/Paris')).toBe('—')
    expect(formatDateTime('not a date', 'Europe/Paris')).toBe('—')
  })

  it('writes the year of an instant that is not from this year', () => {
    const today = new Date('2026-10-12T10:00:00Z')
    expect(formatDateTime('2025-10-11T20:52:12Z', 'Europe/Paris', true, today)).toBe('11 Oct 2025, 22:52:12')
    expect(formatDateTime('2025-10-11T20:52:12Z', 'Europe/Paris', false, today)).toBe('11 Oct 2025, 22:52')
    // The year is the one in the trading timezone: 23:30 UTC on 31 December is already January in Paris.
    expect(formatDateTime('2025-12-31T23:30:00Z', 'Europe/Paris', false, today)).toBe('01 Jan, 00:30')
    expect(formatDateTime('2026-12-31T23:30:00Z', 'Europe/Paris', false, today)).toBe('01 Jan 2027, 00:30')
  })

  it('shows a time the server wrote in its own timezone as it is, in the usual form', () => {
    const today = new Date('2026-10-12T10:00:00Z')
    expect(formatWallTime('2026-10-12 15:12:00', today)).toBe('12 Oct, 15:12:00')
    expect(formatWallTime('2025-12-31 23:59:59', today)).toBe('31 Dec 2025, 23:59:59')
    expect(formatWallTime('yesterday', today)).toBe('yesterday')
    expect(formatWallTime(null, today)).toBe('—')
  })

  it('shows a calendar day without shifting it across timezones', () => {
    expect(formatDay('2026-10-01')).toBe('01 Oct 2026')
    expect(formatDay('2026-10-01', false)).toBe('01 Oct')
    expect(formatDay(null)).toBe('—')
  })

  it('formats durations and relative times', () => {
    expect(formatDuration(42_000)).toBe('42 s')
    expect(formatDuration(5 * 60_000)).toBe('5 min')
    expect(formatDuration(125 * 60_000)).toBe('2 h 05')
    expect(formatDuration(4 * 24 * 3_600_000)).toBe('4 d')

    const now = Date.parse('2026-10-11T12:00:00Z')
    expect(formatRelative('2026-10-11T12:05:00Z', now)).toBe('in 5 min')
    expect(formatRelative('2026-10-11T11:42:00Z', now)).toBe('18 min ago')
  })

  it('keeps the seconds of a length of time measured to the ten seconds', () => {
    expect(formatSpan(10)).toBe('10 s')
    expect(formatSpan(59)).toBe('59 s')
    expect(formatSpan(60)).toBe('1 min')
    expect(formatSpan(110)).toBe('1 min 50 s')
    expect(formatSpan(3590)).toBe('59 min 50 s')
    expect(formatSpan(3600)).toBe('1 h 00 min')
    expect(formatSpan(7510)).toBe('2 h 05 min')
    expect(formatSpan(-5)).toBe('0 s')
  })

  it('turns a local date into the day the API expects', () => {
    expect(toIsoDay(new Date(2026, 0, 5))).toBe('2026-01-05')
  })

  it('builds preset ranges that include today', () => {
    const range = lastDays(7, new Date(2026, 9, 11))
    expect(rangeParams(range)).toEqual({ from: '2026-10-05', to: '2026-10-11' })
    expect(rangeParams({ from: null, to: null })).toEqual({ from: null, to: null })
  })
})

describe('toneOf', () => {
  it('is neutral for zero and for missing values', () => {
    expect(toneOf(1)).toBe('positive')
    expect(toneOf(-0.01)).toBe('negative')
    expect(toneOf(0)).toBe('neutral')
    expect(toneOf(null)).toBe('neutral')
  })
})
