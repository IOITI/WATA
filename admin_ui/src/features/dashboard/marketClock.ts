/**
 * The US open (9:30 in New York) as the session's time zone reads it, and how many hours apart the two are today.
 * New York and Europe change their clocks on different Sundays (in 2026: 8 and 29 March, 25 October and 1 November),
 * so for a week or three a year the open comes an hour earlier or later in Paris than it usually does.
 */

export const NEW_YORK = 'America/New_York'

/** Minutes the zone is ahead of UTC at the instant (negative west of Greenwich). */
export function zoneOffsetMinutes(instant: number, timeZone: string): number {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone,
    hourCycle: 'h23',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  }).formatToParts(new Date(instant))
  const part = (type: Intl.DateTimeFormatPartTypes) => Number(parts.find((item) => item.type === type)?.value ?? 0)
  const asUtc = Date.UTC(part('year'), part('month') - 1, part('day'), part('hour'), part('minute'), part('second'))
  return Math.round((asUtc - Math.floor(instant / 1000) * 1000) / 60_000)
}

/** The instant of 9:30 in New York on New York's day of `now`. */
export function usOpenAt(now: number): number {
  const parts = new Intl.DateTimeFormat('en-US', { timeZone: NEW_YORK, year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(
    new Date(now),
  )
  const part = (type: Intl.DateTimeFormatPartTypes) => Number(parts.find((item) => item.type === type)?.value ?? 0)
  const nineThirty = Date.UTC(part('year'), part('month') - 1, part('day'), 9, 30)
  return nineThirty - zoneOffsetMinutes(nineThirty, NEW_YORK) * 60_000
}

/** A zone's city, as its name says it: "Europe/Paris" is Paris, "America/New_York" New York. */
export function cityOfZone(timeZone: string): string {
  return (timeZone.split('/').pop() ?? timeZone).replace(/_/g, ' ')
}

export interface MarketClock {
  /** "15:30": the US open in the session's zone. */
  openHere: string
  /** Hours from New York to the session's zone, now. */
  hoursApart: number
}

export function marketClock(now: number, timeZone: string): MarketClock {
  const openHere = new Intl.DateTimeFormat('en-GB', { timeZone, hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(
    new Date(usOpenAt(now)),
  )
  const hoursApart = (zoneOffsetMinutes(now, timeZone) - zoneOffsetMinutes(now, NEW_YORK)) / 60
  return { openHere, hoursApart }
}
