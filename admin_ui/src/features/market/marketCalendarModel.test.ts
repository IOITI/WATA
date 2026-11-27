import { describe, expect, it } from 'vitest'

import { configKeyHref } from '@/features/config/catalog'
import type { ConfigClosedBlock, ReleaseBlock } from '@/lib/types'
import { CHRISTMAS_EVE, CPI, DAY_AFTER, marketCalendar, NEW_YEAR, THANKSGIVING } from '@/test/marketCalendar'

import {
  blockLabel,
  blockName,
  blockSentence,
  chosenYear,
  countByEffect,
  dayHref,
  dayLabel,
  dayOffLine,
  daysOfYear,
  monthsOf,
  nextDayOff,
  nextWindow,
  relativeLabel,
  todayTile,
  yearsOf,
} from './marketCalendarModel'

describe('what each block says', () => {
  it('says a US holiday closes the day', () => {
    const [block] = THANKSGIVING.blocks
    expect(blockLabel(block)).toBe('US holiday')
    expect(blockName(block)).toBe('Thanksgiving Day')
    expect(blockSentence(block, 'Europe/Paris')).toBe('The US market is closed: no entry all day.')
  })

  it('says when an early close ends entries and sells what is held, in Paris with New York’s close', () => {
    expect(blockSentence(DAY_AFTER.blocks[0], 'Europe/Paris')).toBe(
      'The US market closes at 13:00 New York (19:00 Paris): no entry from 18:30, what is held is sold at 18:55.',
    )
    // Without the settings that make them, only the close.
    expect(blockSentence(CHRISTMAS_EVE.blocks[0], 'Europe/Paris')).toBe('The US market closes at 13:00 New York (19:00 Paris).')
  })

  it('says a release’s window, and the sale before it when the rule sells', () => {
    const release = CPI.blocks[0] as ReleaseBlock
    expect(blockLabel(release)).toBe('CPI')
    expect(blockName(release)).toBe('CPI for 2026-11')
    expect(blockSentence(release, 'Europe/Paris')).toBe('No entry from 14:25 to 14:45 around CPI (08:30 New York, 14:30 Paris).')
    const flat = { ...release, flat_from: '2026-12-10T13:20:00+00:00' }
    expect(blockSentence(flat, 'Europe/Paris')).toBe(
      'No entry from 14:25 to 14:45 around CPI (08:30 New York, 14:30 Paris); what is held is sold from 14:20.',
    )
    expect(blockName({ ...release, event: 'FOMC', covers: 'statement' })).toBe('FOMC statement')
  })

  it('says a closed date of config.json the US calendar has already can go', () => {
    const config = NEW_YEAR.blocks[1] as ConfigClosedBlock
    expect(blockLabel(config)).toBe('Closed date')
    expect(blockName(config)).toBe('In config.json (01/01/2027)')
    expect(blockSentence(config, 'Europe/Paris')).toBe('No entry all day. The US calendar closes it already: it can be removed from config.json.')
    expect(blockSentence({ ...config, also_us: false }, 'Europe/Paris')).toBe('No entry all day: a closed date of config.json.')
  })
})

describe('the days', () => {
  it('names a day with its weekday, and yesterday as yesterday', () => {
    expect(dayLabel('2026-11-26')).toBe('Thu 26 Nov 2026')
    expect(dayLabel('2026-11-26', false)).toBe('Thu 26 Nov')
    expect(relativeLabel('2026-11-26', '2026-11-27')).toBe('Yesterday')
    expect(relativeLabel('2026-11-27', '2026-11-27')).toBe('Today')
    expect(relativeLabel('2027-01-01', '2026-12-31')).toBe('Tomorrow')
    expect(relativeLabel('2026-12-24', '2026-11-27')).toBeNull()
  })

  it('says what today is: a blocked day, a weekend, or an ordinary day', () => {
    expect(todayTile(marketCalendar())).toEqual({
      value: 'Closes early',
      hint: 'The US market closes at 13:00 New York (19:00 Paris): no entry from 18:30, what is held is sold at 18:55.',
    })
    expect(todayTile(marketCalendar({ today: '2026-11-28' }))).toEqual({
      value: 'Weekend',
      hint: 'Sat 28 Nov 2026: no trading on Saturdays and Sundays',
    })
    expect(todayTile(marketCalendar({ today: '2026-11-30' })).value).toBe('Ordinary day')
  })

  it('offers each year known and the current one, choosing the one asked only when known', () => {
    const calendar = marketCalendar()
    expect(yearsOf(calendar)).toEqual([2026, 2027])
    expect(yearsOf(marketCalendar({ days: [] }))).toEqual([2026])
    expect(chosenYear(calendar, '2027')).toBe(2027)
    expect(chosenYear(calendar, '1999')).toBe(2026)
    expect(chosenYear(calendar, null)).toBe(2026)
  })

  it('lists a year’s days, its release windows left out when asked', () => {
    const calendar = marketCalendar()
    expect(daysOfYear(calendar, 2026).map((day) => day.day)).toEqual(['2026-11-26', '2026-11-27', '2026-12-10', '2026-12-24'])
    expect(daysOfYear(calendar, 2026, false).map((day) => day.day)).toEqual(['2026-11-26', '2026-11-27', '2026-12-24'])
    expect(countByEffect(daysOfYear(calendar, 2026))).toEqual({ closed: 1, early_close: 2, window: 1 })
  })

  it('finds the next day off after today, over a release window and across the year’s end', () => {
    expect(nextDayOff(marketCalendar())?.day).toBe('2026-12-24')
    expect(nextDayOff(marketCalendar({ today: '2026-12-24' }))?.day).toBe('2027-01-01')
    expect(nextDayOff(marketCalendar({ today: '2027-01-01' }))).toBeNull()
    expect(nextWindow(marketCalendar())?.day).toBe('2026-12-10')
    expect(nextWindow(marketCalendar({ today: '2026-12-10' }))?.day).toBe('2026-12-10')
  })

  it('says the next day off on one line, the year only when it is another', () => {
    expect(dayOffLine(CHRISTMAS_EVE, '2026-11-27')).toBe('Thu 24 Dec: the US market closes early at 13:00 New York (Christmas Eve)')
    expect(dayOffLine(NEW_YEAR, '2026-12-24')).toBe("Fri 01 Jan 2027: the US market is closed (New Year's Day)")
    expect(dayOffLine(NEW_YEAR, '2026-12-31')).toBe("Tomorrow, Fri 01 Jan: the US market is closed (New Year's Day)")
  })

  it('leads to a day’s line on its year’s page, and to where config.json sets a kind', () => {
    expect(dayHref('2026-12-24')).toBe('/market-calendar?year=2026#d-2026-12-24')
    expect(configKeyHref(marketCalendar().closed_dates.link!)).toBe('/config/hours#b-closed')
    expect(configKeyHref({ config: { group: 'hours', path: 'trade.rules[market_closed_dates].rule_config.market_closed_dates' } })).toBe(
      '/config/hours#p-trade-rules-market-closed-dates-rule-config-market-closed-dates',
    )
  })
})

describe('the year at a glance', () => {
  const months = monthsOf(2026, daysOfYear(marketCalendar(), 2026), '2026-11-27')

  it('has twelve months of weeks starting on Monday', () => {
    expect(months.map((month) => month.label)).toHaveLength(12)
    const november = months[10]
    expect(november.label).toBe('November')
    // 1 November 2026 is a Sunday: six empty days before it.
    expect(november.weeks[0].slice(0, 6)).toEqual([null, null, null, null, null, null])
    expect(november.weeks[0][6]).toMatchObject({ day: '2026-11-01', date: 1, weekend: true })
    expect(november.weeks.every((week) => week.length === 7)).toBe(true)
  })

  it('puts each blocked day in its cell, today marked and the days before it past', () => {
    const cells = months[10].weeks.flat().filter((cell) => cell !== null)
    const at = (day: string) => cells.find((cell) => cell!.day === day)!
    expect(at('2026-11-26')).toMatchObject({ past: true, today: false, blocked: THANKSGIVING })
    expect(at('2026-11-27')).toMatchObject({ past: false, today: true, blocked: DAY_AFTER })
    expect(at('2026-11-30')).toMatchObject({ weekend: false, blocked: null })
  })
})
