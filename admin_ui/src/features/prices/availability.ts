import { formatDateTime, formatNumber, formatPercent, formatSpan, formatTime } from 'ioiti-ui'

import type { InstrumentAvailability, PriceAvailability, PriceInstrument, PriceStretch } from '@/lib/types'

import { valueDecimals } from './chartData'

/**
 * What the list of instruments says of each of them beside its name: its last
 * quote, what was recorded of it, and what is missing from it. Sentences and
 * figures only, so that all of it can be tested without a page.
 */

/** What the server worked out for one instrument; undefined while it is not known. */
export function availabilityOf(
  availability: PriceAvailability | undefined,
  instrument: PriceInstrument,
): InstrumentAvailability | undefined {
  // Worked out for the instruments of the recorder alone, by their id.
  if (instrument.id === null) return undefined
  return availability?.items?.[String(instrument.id)]
}

/** Saxo left it without a quote at some point: what the mark in the list means. */
export function isFlagged(item: InstrumentAvailability | undefined): boolean {
  return item !== undefined && item.issues.length > 0
}

/** The share of the time it was followed that it had a quote, in percent. */
export function quotedShare(item: InstrumentAvailability): number | null {
  const followed = item.quoted_seconds + item.missing_seconds
  return followed > 0 ? (100 * item.quoted_seconds) / followed : null
}

function longestHole(item: InstrumentAvailability): PriceStretch | undefined {
  return item.stretches
    .filter((stretch) => stretch.reason === 'unquoted')
    .reduce<PriceStretch | undefined>(
      (found, stretch) => (found === undefined || stretch.seconds > found.seconds ? stretch : found),
      undefined,
    )
}

function sentence(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1)
}

/** One day: the time is enough. Several: the day is said too. */
function moment(value: string, timeZone: string, severalDays: boolean): string {
  return severalDays ? formatDateTime(value, timeZone) : formatTime(value, timeZone)
}

function holesText(item: InstrumentAvailability, timeZone: string, severalDays: boolean): string {
  const worst = longestHole(item)
  if (item.holes === 1 && worst) {
    return `no quote for ${formatSpan(worst.seconds)} from ${moment(worst.start, timeZone, severalDays)}`
  }
  const longest = worst ? `, the longest ${formatSpan(worst.seconds)} from ${moment(worst.start, timeZone, severalDays)}` : ''
  return `${formatNumber(item.holes)} holes in its quotes, ${formatSpan(item.hole_seconds)} in all${longest}`
}

function stopText(item: InstrumentAvailability, timeZone: string, severalDays: boolean): string {
  const others = item.stops.length - 1
  const again = others > 0 ? `, and on ${others === 1 ? 'one other day' : `${others} other days`}` : ''
  return `no more quote after ${moment(item.stops[0], timeZone, severalDays)}${again}`
}

/** What is missing, in a few words: the mark's own text, for who cannot see its colour or hover it. */
export function flagText(item: InstrumentAvailability, timeZone: string, severalDays: boolean): string {
  const parts: string[] = []
  if (item.holes > 0) {
    // In the list a count is enough: when and how long is in the row once opened.
    parts.push(item.holes === 1 ? holesText(item, timeZone, severalDays) : `${formatNumber(item.holes)} holes in its quotes`)
  }
  if (item.stops.length > 0) parts.push(stopText(item, timeZone, severalDays))
  return parts.length > 0 ? `Missing data: ${parts.join(', ')}` : ''
}

export interface Fact {
  label: string
  value: string
  /** Something is wrong with it. */
  warning?: boolean
}

function lastPrice(instrument: PriceInstrument, timeZone: string): Fact {
  const { last_bid: bid, last_ask: ask, last_t: at } = instrument
  if (bid === null || bid === undefined) return { label: 'Last price', value: '—' }
  const decimals = valueDecimals([bid, ask ?? null])
  const when = at ? ` at ${formatTime(new Date(at * 1000).toISOString(), timeZone)}` : ''
  // An index has no bid and ask of its own: one level.
  if (ask === null || ask === undefined || ask === bid) {
    return { label: 'Last price', value: `${formatNumber(bid, decimals)}${when}` }
  }
  return {
    label: 'Last price',
    value: `Bid ${formatNumber(bid, decimals)} · Ask ${formatNumber(ask, decimals)} · spread ${formatNumber(ask - bid, decimals)}${when}`,
  }
}

/** How much of the time it had a quote, and how much it did not while other instruments had one. */
function missing(item: InstrumentAvailability): Fact {
  const share = quotedShare(item)
  // Rounded down: never a hundred while something is missing.
  const quoted = share === null ? '' : `${formatPercent(Math.floor(share * 10) / 10, false, 1)} · `
  if (item.missing_seconds === 0) return { label: 'Availability', value: `${quoted}nothing missing between its bars` }
  return {
    label: 'Availability',
    value: `${quoted}${formatSpan(item.missing_seconds)} without a quote while other instruments had one`,
    warning: true,
  }
}

/**
 * What an opened row of the list says. `availability` undefined: still being
 * read; with `items` null: not worked out for a period that long.
 */
export function instrumentFacts(
  instrument: PriceInstrument,
  availability: PriceAvailability | undefined,
  timeZone: string,
): Fact[] {
  const item = availabilityOf(availability, instrument)
  const severalDays = availability !== undefined && availability.from !== availability.to
  const facts: Fact[] = [
    lastPrice(instrument, timeZone),
    {
      // What Saxo counted as traded over the period.
      label: 'Volume',
      value:
        instrument.volume === null || instrument.volume === undefined ? 'Not recorded' : formatNumber(instrument.volume),
    },
  ]

  if (item) {
    facts.push(
      { label: 'Quote changes', value: formatNumber(item.updates) },
      {
        label: 'Recorded',
        value: `${moment(item.first_bar, timeZone, severalDays)} to ${moment(item.last_bar, timeZone, severalDays)} · ${formatNumber(item.bars)} bars`,
      },
      missing(item),
    )
    if (item.holes > 0) {
      facts.push({ label: 'Holes', value: sentence(holesText(item, timeZone, severalDays)), warning: true })
    }
    if (item.stops.length > 0) {
      facts.push({
        label: 'Stopped',
        value: `${sentence(stopText(item, timeZone, severalDays))}, while other turbos were still quoted`,
        warning: true,
      })
    }
    if (item.knocked_out_at) {
      facts.push({
        label: 'Knocked out',
        value: `Probably, around ${moment(item.knocked_out_at, timeZone, severalDays)}: its bid had fallen to a fraction of the day's highest`,
      })
    }
    if (item.without_ask > 0) {
      facts.push({ label: 'Ask', value: `Missing on ${formatNumber(item.without_ask)} of its bars` })
    }
    if (item.delayed_minutes > 0) {
      facts.push({ label: 'Delay', value: `Quotes received up to ${item.delayed_minutes} min late` })
    }
  } else if (availability && availability.items === null) {
    facts.push({
      label: 'Availability',
      value: `Not worked out for a period of more than ${availability.max_days} days`,
    })
  } else if (availability) {
    // Listed, and nothing found of it: its bars came between the two answers.
    facts.push({ label: 'Availability', value: '—' })
  }

  facts.push({
    label: 'Instrument',
    value: [instrument.symbol, instrument.uic === null ? null : `UIC ${instrument.uic}`, instrument.currency]
      .filter(Boolean)
      .join(' · '),
  })
  return facts
}
