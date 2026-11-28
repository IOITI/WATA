// The Market calendar page's words and shapes, and the dashboard's next day off (GET /api/market-calendar,
// src/admin/blocked_days.py): every day the trader takes no entry, or fewer. The server reads the files and rules the
// trader reads; this module only says what it found, day by day. Every US release is listed, applied or not: those
// not applied block nothing, and the page shows them only when asked (`ReleaseView`). Pure.

import { formatDay } from 'ioiti-ui'
import type { StatusTone } from 'ioiti-ui'

import { cityOfZone, NEW_YORK } from '@/features/dashboard/marketClock'
import type { BlockedDay, DayBlock, DayEffect, MarketCalendar, ReleaseBlock } from '@/lib/types'

export const PAGE_PATH = '/market-calendar'

const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October',
  'November', 'December']
const DAY_MS = 86_400_000

/** What each effect is called, the strongest first. */
export const EFFECT_LABELS: Record<DayEffect, string> = {
  closed: 'Closed all day',
  early_close: 'Closes early',
  window: 'Release window',
  none: 'Release not applied',
}

/** The tone of each effect: a day closed stops everything, an early close part of it, a window minutes. */
export const EFFECT_TONES: Record<DayEffect, StatusTone> = {
  closed: 'danger',
  early_close: 'warning',
  window: 'info',
  none: 'neutral',
}

function clock(iso: string, timeZone: string): string {
  return new Intl.DateTimeFormat('en-GB', { timeZone, hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(iso))
}

/** "Thu 26 Nov 2026": a day as the list and the dashboard say it, never shifted across time zones. */
export function dayLabel(day: string, withYear = true): string {
  const [year, month, date] = day.split('-').map(Number)
  const weekday = new Date(Date.UTC(year, month - 1, date)).getUTCDay()
  return `${WEEKDAYS[(weekday + 6) % 7]} ${formatDay(day, withYear)}`
}

function daysBetween(from: string, to: string): number {
  const at = (day: string) => {
    const [year, month, date] = day.split('-').map(Number)
    return Date.UTC(year, month - 1, date)
  }
  return Math.round((at(to) - at(from)) / DAY_MS)
}

/** Saturday or Sunday: the trader's hours never open on them, so no weekend day is listed. */
export function isWeekend(day: string): boolean {
  const [year, month, date] = day.split('-').map(Number)
  const weekday = new Date(Date.UTC(year, month - 1, date)).getUTCDay()
  return weekday === 0 || weekday === 6
}

/** What today is for the trader's calendar: a blocked day's effect, a weekend, or an ordinary day. */
export function todayTile(calendar: MarketCalendar): { value: string; hint: string } {
  const today = calendar.days.find((day) => day.day === calendar.today)
  if (today && today.effect !== 'none') return { value: EFFECT_LABELS[today.effect], hint: blockSentence(today.blocks[0], calendar.timezone) }
  if (isWeekend(calendar.today)) return { value: 'Weekend', hint: `${dayLabel(calendar.today)}: no trading on Saturdays and Sundays` }
  // A release not applied blocks nothing: an ordinary day, said with it.
  const release = today?.blocks.find((block): block is ReleaseBlock => block.kind === 'release')
  const aside = release ? ` (${release.event} at ${release.time_new_york} New York, not applied)` : ''
  return { value: 'Ordinary day', hint: `${dayLabel(calendar.today)}: the trading hours as usual${aside}` }
}

/** "Today", "Yesterday", "Tomorrow", or nothing for a day further away. */
export function relativeLabel(day: string, today: string): string | null {
  const offset = daysBetween(today, day)
  return offset === 0 ? 'Today' : offset === -1 ? 'Yesterday' : offset === 1 ? 'Tomorrow' : null
}

/** What a block is called: its badge. */
export function blockLabel(block: DayBlock): string {
  switch (block.kind) {
    case 'us_closed':
      return 'US holiday'
    case 'us_early_close':
      return 'US early close'
    case 'config_closed':
      return 'Closed date'
    case 'release':
      return block.event
  }
}

export function blockTone(block: DayBlock): StatusTone {
  switch (block.kind) {
    case 'us_closed':
    case 'config_closed':
      return 'danger'
    case 'us_early_close':
      return 'warning'
    case 'release':
      return block.applies ? 'info' : 'neutral'
  }
}

/** What it is: the holiday's name, the release and the month it covers. */
export function blockName(block: DayBlock): string {
  switch (block.kind) {
    case 'us_closed':
    case 'us_early_close':
      return block.name
    case 'config_closed':
      return `In config.json (${block.written})`
    case 'release':
      return block.covers === 'statement' ? `${block.event} statement` : `${block.event} for ${block.covers}`
  }
}

/**
 * What WATA does because of it, in the trading timezone (and New York's where its hour is New York's): what the
 * trader refuses, what the position monitor sells.
 */
export function blockSentence(block: DayBlock, timeZone: string): string {
  const city = cityOfZone(timeZone)
  switch (block.kind) {
    case 'us_closed':
      return 'The US market is closed: no entry all day.'
    case 'config_closed':
      return block.also_us
        ? 'No entry all day. The US calendar closes it already: it can be removed from config.json.'
        : 'No entry all day: a closed date of config.json.'
    case 'us_early_close': {
      const closes = `The US market closes at ${block.close_new_york} New York (${clock(block.closes_at, timeZone)} ${city})`
      const entries = block.entries_end_at ? `no entry from ${clock(block.entries_end_at, timeZone)}` : null
      const sale = block.sale_at ? `what is held is sold at ${clock(block.sale_at, timeZone)}` : null
      const parts = [entries, sale].filter((part): part is string => part !== null)
      return parts.length > 0 ? `${closes}: ${parts.join(', ')}.` : `${closes}.`
    }
    case 'release': {
      const window = `no entry from ${clock(block.start, timeZone)} to ${clock(block.end, timeZone)}`
      const released = `${block.time_new_york} New York, ${clock(block.released_at, timeZone)} ${city}`
      const flat = block.flat_from ? `; what is held is sold from ${clock(block.flat_from, timeZone)}` : ''
      const said = `${window} around ${block.event} (${released})${flat}.`
      if (block.applies) return said.charAt(0).toUpperCase() + said.slice(1)
      switch (block.not_applied) {
        case 'not_named':
          return `Not applied: the macro_news rule does not name ${block.event}. Named, ${said}`
        case 'rule_error':
          return `Not applied: the macro_news rule could not be read. With its default minutes, ${said}`
        default:
          return `Not applied: the macro_news rule is off. With its default minutes, ${said}`
      }
    }
  }
}

/** What a day does once some of its blocks are left out: the strongest of those kept. */
export function effectOf(blocks: DayBlock[]): DayEffect {
  if (blocks.some((block) => block.kind === 'us_closed' || block.kind === 'config_closed')) return 'closed'
  if (blocks.some((block) => block.kind === 'us_early_close')) return 'early_close'
  if (blocks.some((block) => block.kind === 'release' && block.applies)) return 'window'
  return 'none'
}

/**
 * Which releases the page shows: the windows applied (`windows`), those not applied (`unapplied`, blocking nothing),
 * of the kinds chosen (`kinds`; null for every kind). Kept in the address: `?releases=hide`, `?unapplied=show`,
 * `?kinds=FOMC,CPI`.
 */
export interface ReleaseView {
  windows: boolean
  unapplied: boolean
  kinds: string[] | null
}

/** Every applied window, nothing else: the counts, and the page as it opens. */
export const APPLIED_ONLY: ReleaseView = { windows: true, unapplied: false, kinds: null }

/** The kinds shown until others are chosen: the rule's, and the four a rule that names none takes. */
export function defaultKinds(calendar: MarketCalendar): string[] {
  const wanted = new Set([...calendar.news.events, ...calendar.news.default_events])
  return calendar.news.all_events.filter((kind) => wanted.has(kind))
}

export function releaseView(params: URLSearchParams, calendar: MarketCalendar): ReleaseView {
  const asked = params.get('kinds')
  return {
    windows: params.get('releases') !== 'hide',
    unapplied: params.get('unapplied') === 'show',
    kinds: asked === null ? defaultKinds(calendar) : asked.split(',').filter((kind) => calendar.news.all_events.includes(kind)),
  }
}

/** The address's parameters with the view changed, the defaults left out. */
export function withView(params: URLSearchParams, view: ReleaseView, calendar: MarketCalendar): URLSearchParams {
  const next = new URLSearchParams(params)
  if (view.windows) next.delete('releases')
  else next.set('releases', 'hide')
  if (view.unapplied) next.set('unapplied', 'show')
  else next.delete('unapplied')
  const defaults = defaultKinds(calendar)
  const kinds = view.kinds ?? calendar.news.all_events
  const same = kinds.length === defaults.length && defaults.every((kind) => kinds.includes(kind))
  if (same) next.delete('kinds')
  else next.set('kinds', calendar.news.all_events.filter((kind) => kinds.includes(kind)).join(','))
  return next
}

/** Whether the view shows releases at all (the kinds filter is offered only then). */
export function showsReleases(view: ReleaseView, calendar: MarketCalendar): boolean {
  return (calendar.news.on && view.windows) || view.unapplied
}

/** The years the calendar knows a day of, and the market's current year, oldest first. */
export function yearsOf(calendar: MarketCalendar): number[] {
  const years = new Set(calendar.days.map((day) => Number(day.day.slice(0, 4))))
  years.add(Number(calendar.today.slice(0, 4)))
  return [...years].sort((a, b) => a - b)
}

/** The year asked for when the calendar has it, else the market's current year. */
export function chosenYear(calendar: MarketCalendar, asked: string | null): number {
  const year = Number(asked)
  return yearsOf(calendar).includes(year) ? year : Number(calendar.today.slice(0, 4))
}

/** A year's days, oldest first, with the releases the view shows; a day left without a block is dropped. */
export function daysOfYear(calendar: MarketCalendar, year: number, view: ReleaseView = APPLIED_ONLY): BlockedDay[] {
  const shown = (block: DayBlock) =>
    block.kind !== 'release' ||
    ((view.kinds === null || view.kinds.includes(block.event)) && (block.applies ? view.windows : view.unapplied))
  return calendar.days
    .filter((day) => day.day.startsWith(`${year}-`))
    .map((day) => {
      const blocks = day.blocks.filter(shown)
      return blocks.length === day.blocks.length ? day : { ...day, blocks, effect: effectOf(blocks) }
    })
    .filter((day) => day.blocks.length > 0)
}

/** How many days of each effect a list holds. */
export function countByEffect(days: BlockedDay[]): Record<DayEffect, number> {
  const counts: Record<DayEffect, number> = { closed: 0, early_close: 0, window: 0, none: 0 }
  for (const day of days) counts[day.effect] += 1
  return counts
}

/** The next day after today the market closes, or closes early (a closed date of config.json too); null when none is known. */
export function nextDayOff(calendar: MarketCalendar): BlockedDay | null {
  return calendar.days.find((day) => day.day > calendar.today && (day.effect === 'closed' || day.effect === 'early_close')) ?? null
}

/** The next release window applied, from today on, today's included; null while the macro_news rule is off or none is known. */
export function nextWindow(calendar: MarketCalendar): BlockedDay | null {
  return (
    calendar.days.find((day) => day.day >= calendar.today && day.blocks.some((block) => block.kind === 'release' && block.applies)) ??
    null
  )
}

/** The next release of these kinds from today on, applied or not, with its day. */
export function nextRelease(calendar: MarketCalendar, kinds: string[]): { day: BlockedDay; release: ReleaseBlock } | null {
  for (const day of calendar.days) {
    if (day.day < calendar.today) continue
    const release = day.blocks.find((block): block is ReleaseBlock => block.kind === 'release' && kinds.includes(block.event))
    if (release) return { day, release }
  }
  return null
}

/** "CPI on Thu 10 Dec at 08:30 New York (not applied)": the tile's line while the rule is off. */
export function nextReleaseLine(next: { day: BlockedDay; release: ReleaseBlock }, today: string): string {
  const relative = relativeLabel(next.day.day, today)
  const when = relative ? `${relative.toLowerCase()}` : `on ${dayLabel(next.day.day, next.day.day.slice(0, 4) !== today.slice(0, 4))}`
  return `${next.release.event} ${when} at ${next.release.time_new_york} New York (${next.release.applies ? 'applied' : 'not applied'})`
}

/** What a day off is, by its strongest block: "the US market closes early at 13:00 New York (Christmas Eve)". */
export function dayOffWhat(day: BlockedDay): string {
  const first = day.blocks[0]
  switch (first.kind) {
    case 'us_early_close':
      return `the US market closes early at ${first.close_new_york} New York (${first.name})`
    case 'us_closed':
      return `the US market is closed (${first.name})`
    case 'config_closed':
      return 'a closed date of config.json'
    case 'release':
      return `${first.event} at ${first.time_new_york} New York`
  }
}

/** "Thu 24 Dec: the US market closes early at 13:00 New York (Christmas Eve)": the dashboard's line. */
export function dayOffLine(day: BlockedDay, today: string): string {
  const relative = relativeLabel(day.day, today)
  const when = relative ? `${relative}, ${dayLabel(day.day, false)}` : dayLabel(day.day, day.day.slice(0, 4) !== today.slice(0, 4))
  return `${when}: ${dayOffWhat(day)}`
}

export function dayAnchor(day: string): string {
  return `d-${day}`
}

/** The page at the year of a day, scrolled to its row. */
export function dayHref(day: string): string {
  return `${PAGE_PATH}?year=${day.slice(0, 4)}#${dayAnchor(day)}`
}

export interface MonthCell {
  day: string
  date: number
  weekend: boolean
  today: boolean
  past: boolean
  /** Its blocked day, when it is one. */
  blocked: BlockedDay | null
}

export interface Month {
  month: number
  label: string
  /** Its weeks, Monday first; a day of the month before or after is null. */
  weeks: (MonthCell | null)[][]
}

/** A year's twelve months as grids of weeks, each blocked day in its cell. */
export function monthsOf(year: number, days: BlockedDay[], today: string): Month[] {
  const blocked = new Map(days.map((day) => [day.day, day]))
  return MONTHS.map((label, index) => {
    const length = new Date(Date.UTC(year, index + 1, 0)).getUTCDate()
    const lead = (new Date(Date.UTC(year, index, 1)).getUTCDay() + 6) % 7
    const cells: (MonthCell | null)[] = Array.from({ length: lead }, () => null)
    for (let date = 1; date <= length; date += 1) {
      const day = `${year}-${String(index + 1).padStart(2, '0')}-${String(date).padStart(2, '0')}`
      const weekday = (lead + date - 1) % 7
      cells.push({ day, date, weekend: weekday >= 5, today: day === today, past: day < today, blocked: blocked.get(day) ?? null })
    }
    while (cells.length % 7 !== 0) cells.push(null)
    const weeks = Array.from({ length: cells.length / 7 }, (_, week) => cells.slice(week * 7, week * 7 + 7))
    return { month: index + 1, label, weeks }
  })
}

/** Where WATA's hours are read, when it is not New York. */
export function zonesLine(calendar: MarketCalendar): string {
  const trading = cityOfZone(calendar.timezone)
  return calendar.timezone === NEW_YORK
    ? 'Times are New York’s.'
    : `Times are ${trading}’s, with New York’s where the hour is New York’s (the US closes and the releases).`
}
