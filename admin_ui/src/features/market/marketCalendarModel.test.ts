import { describe, expect, it } from 'vitest'

import { configKeyHref } from '@/features/config/catalog'
import type { ConfigClosedBlock, ReleaseBlock } from '@/lib/types'
import { CHRISTMAS_EVE, CPI, DAY_AFTER, marketCalendar, NEW_YEAR, PPI, ruleOff, THANKSGIVING } from '@/test/marketCalendar'

import {
  blockLabel,
  blockName,
  blockSentence,
  blockTone,
  chosenYear,
  countByEffect,
  dayHref,
  dayLabel,
  dayOffLine,
  daysOfYear,
  defaultKinds,
  effectOf,
  monthsOf,
  nextDayOff,
  nextRelease,
  nextReleaseLine,
  nextWindow,
  relativeLabel,
  releaseView,
  showsReleases,
  todayTile,
  withView,
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

  it('says a release not applied blocks nothing, and the window it would have', () => {
    const notNamed = PPI.blocks[0] as ReleaseBlock
    expect(blockTone(notNamed)).toBe('neutral')
    expect(blockTone(CPI.blocks[0])).toBe('info')
    expect(blockSentence(notNamed, 'Europe/Paris')).toBe(
      'Not applied: the macro_news rule does not name PPI. Named, no entry from 14:25 to 14:45 around PPI (08:30 New York, 14:30 Paris).',
    )
    const off = ruleOff().days.find((day) => day.day === '2026-12-10')!.blocks[0]
    expect(blockSentence(off, 'Europe/Paris')).toBe(
      'Not applied: the macro_news rule is off. With its default minutes, no entry from 14:25 to 14:45 around CPI (08:30 New York, 14:30 Paris).',
    )
    expect(blockSentence({ ...notNamed, not_applied: 'rule_error' }, 'Europe/Paris')).toMatch(
      /^Not applied: the macro_news rule could not be read\. With its default minutes, no entry from 14:25/,
    )
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
    // A release not applied blocks nothing: an ordinary day, said with it.
    expect(todayTile(marketCalendar({ today: '2026-12-15' }))).toEqual({
      value: 'Ordinary day',
      hint: 'Tue 15 Dec 2026: the trading hours as usual (PPI at 08:30 New York, not applied)',
    })
  })

  it('offers each year known and the current one, choosing the one asked only when known', () => {
    const calendar = marketCalendar()
    expect(yearsOf(calendar)).toEqual([2026, 2027])
    expect(yearsOf(marketCalendar({ days: [] }))).toEqual([2026])
    expect(chosenYear(calendar, '2027')).toBe(2027)
    expect(chosenYear(calendar, '1999')).toBe(2026)
    expect(chosenYear(calendar, null)).toBe(2026)
  })

  it('lists a year’s days, its release windows left out when asked, those not applied only when asked', () => {
    const calendar = marketCalendar()
    const view = (windows: boolean, unapplied: boolean, kinds: string[] | null = null) => ({ windows, unapplied, kinds })
    const listed = (shown: ReturnType<typeof view>) => daysOfYear(calendar, 2026, shown).map((day) => day.day)
    expect(listed(view(true, false))).toEqual(['2026-11-26', '2026-11-27', '2026-12-10', '2026-12-24'])
    expect(listed(view(false, false))).toEqual(['2026-11-26', '2026-11-27', '2026-12-24'])
    expect(listed(view(true, true))).toEqual(['2026-11-26', '2026-11-27', '2026-12-10', '2026-12-15', '2026-12-24'])
    expect(listed(view(true, true, ['CPI']))).toEqual(['2026-11-26', '2026-11-27', '2026-12-10', '2026-12-24'])
    expect(listed(view(true, true, ['PPI']))).toEqual(['2026-11-26', '2026-11-27', '2026-12-15', '2026-12-24'])
    // The counts are of what blocks: the default view, releases not applied left out.
    expect(countByEffect(daysOfYear(calendar, 2026))).toEqual({ closed: 1, early_close: 2, window: 1, none: 0 })
  })

  it('works a day’s effect out again from what is kept of it', () => {
    const goodFriday = [...THANKSGIVING.blocks, ...CPI.blocks]
    expect(effectOf(goodFriday)).toBe('closed')
    expect(effectOf(CPI.blocks)).toBe('window')
    expect(effectOf(PPI.blocks)).toBe('none')
    // CPI's window hidden, a release not applied shown on the same day: the day blocks nothing as drawn.
    const both = { ...CPI, blocks: [...CPI.blocks, ...PPI.blocks] }
    const days = daysOfYear(marketCalendar({ days: [both] }), 2026, { windows: false, unapplied: true, kinds: null })
    expect(days.map((day) => [day.day, day.effect, day.blocks.length])).toEqual([['2026-12-10', 'none', 1]])
  })

  it('reads the view from the address and writes it back, its defaults left out', () => {
    const calendar = marketCalendar()
    expect(defaultKinds(calendar)).toEqual(['FOMC', 'CPI', 'NFP', 'PCE'])
    expect(defaultKinds(marketCalendar({ news: { ...calendar.news, events: ['PPI'] } }))).toEqual(['FOMC', 'CPI', 'NFP', 'PCE', 'PPI'])
    const opened = releaseView(new URLSearchParams('year=2026'), calendar)
    expect(opened).toEqual({ windows: true, unapplied: false, kinds: ['FOMC', 'CPI', 'NFP', 'PCE'] })
    expect(releaseView(new URLSearchParams('releases=hide&unapplied=show&kinds=PPI,ISM,CPI'), calendar)).toEqual({
      windows: false, unapplied: true, kinds: ['PPI', 'CPI'],
    })
    expect(releaseView(new URLSearchParams('kinds='), calendar).kinds).toEqual([])
    const written = withView(new URLSearchParams('year=2026'), { windows: true, unapplied: true, kinds: ['GDP', 'CPI'] }, calendar)
    expect(written.toString()).toBe('year=2026&unapplied=show&kinds=CPI%2CGDP')
    expect(withView(written, opened, calendar).toString()).toBe('year=2026')
    expect(showsReleases(opened, calendar)).toBe(true)
    expect(showsReleases(opened, ruleOff())).toBe(false)
    expect(showsReleases({ ...opened, unapplied: true }, ruleOff())).toBe(true)
  })

  it('finds the next day off after today, over a release window and across the year’s end', () => {
    expect(nextDayOff(marketCalendar())?.day).toBe('2026-12-24')
    expect(nextDayOff(marketCalendar({ today: '2026-12-24' }))?.day).toBe('2027-01-01')
    expect(nextDayOff(marketCalendar({ today: '2027-01-01' }))).toBeNull()
    expect(nextWindow(marketCalendar())?.day).toBe('2026-12-10')
    expect(nextWindow(marketCalendar({ today: '2026-12-10' }))?.day).toBe('2026-12-10')
    // A release not applied is never a day off nor a window.
    expect(nextDayOff(ruleOff())?.day).toBe('2026-12-24')
    expect(nextWindow(ruleOff())).toBeNull()
    expect(nextWindow(marketCalendar({ today: '2026-12-11' }))).toBeNull()
  })

  it('names the next release, applied or not, for the tile while the rule is off', () => {
    const next = nextRelease(ruleOff(), ['FOMC', 'CPI', 'NFP', 'PCE'])
    expect(next?.day.day).toBe('2026-12-10')
    expect(nextReleaseLine(next!, '2026-11-27')).toBe('CPI on Thu 10 Dec at 08:30 New York (not applied)')
    expect(nextReleaseLine(next!, '2026-12-09')).toBe('CPI tomorrow at 08:30 New York (not applied)')
    expect(nextRelease(ruleOff(), ['PPI'])?.day.day).toBe('2026-12-15')
    expect(nextRelease(ruleOff(), ['GDP'])).toBeNull()
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
