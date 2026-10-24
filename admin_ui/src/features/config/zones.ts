/**
 * The same moment in other timezones, for the hours of the configuration: an
 * hour of the market (New York's, say) shown as it is in Paris and in UTC
 * today, and whether the clocks are changed apart this week, when the usual
 * gap between two cities does not hold.
 */

const PARTS = {
  hourCycle: 'h23',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
} as const

const formatters = new Map<string, Intl.DateTimeFormat>()

function formatter(zone: string): Intl.DateTimeFormat {
  let found = formatters.get(zone)
  if (!found) {
    found = new Intl.DateTimeFormat('en-US', { timeZone: zone, ...PARTS })
    formatters.set(zone, found)
  }
  return found
}

function partsIn(zone: string, instant: Date): Record<string, number> {
  const parts: Record<string, number> = {}
  for (const part of formatter(zone).formatToParts(instant)) {
    if (part.type !== 'literal') parts[part.type] = Number(part.value)
  }
  return parts
}

/** The offset of ``zone`` from UTC at ``instant``, in minutes (Paris in winter: 60). */
export function offsetMinutes(zone: string, instant: Date): number {
  const parts = partsIn(zone, instant)
  const wall = Date.UTC(parts.year, parts.month - 1, parts.day, parts.hour, parts.minute, parts.second)
  return Math.round((wall - Math.floor(instant.getTime() / 1000) * 1000) / 60000)
}

/** The instant of ``hour``:``minute`` on ``day`` (YYYY-MM-DD) in ``zone``. */
export function instantOf(day: string, hour: number, minute: number, zone: string): Date {
  const [year, month, date] = day.split('-').map(Number)
  const wall = Date.UTC(year, month - 1, date, hour, minute)
  // Twice: the offset at the first guess can be the other side of a change of the clocks.
  let instant = wall - offsetMinutes(zone, new Date(wall)) * 60000
  instant = wall - offsetMinutes(zone, new Date(instant)) * 60000
  return new Date(instant)
}

function two(value: number): string {
  return String(value).padStart(2, '0')
}

/** ``instant`` in ``zone`` as YYYY-MM-DD. */
export function dayIn(instant: Date, zone: string): string {
  const parts = partsIn(zone, instant)
  return `${parts.year}-${two(parts.month)}-${two(parts.day)}`
}

/** ``instant`` in ``zone`` as HH:MM. */
export function clockIn(instant: Date, zone: string): string {
  const parts = partsIn(zone, instant)
  return `${two(parts.hour)}:${two(parts.minute)}`
}

/** A timezone as a person names it: America/New_York → New York. */
export function zoneLabel(zone: string): string {
  if (zone === 'UTC' || zone === 'Etc/UTC') return 'UTC'
  return (zone.split('/').at(-1) ?? zone).replaceAll('_', ' ')
}

export interface SameMoment {
  zone: string
  clock: string
  /** −1, 0 or +1: the day before, the same day, the day after. */
  dayShift: number
}

/** ``hour``:``minute`` of today in ``zone``, as it is in each of ``others``. */
export function sameMoment(hour: number, minute: number, zone: string, others: string[], now: Date = new Date()): SameMoment[] {
  const day = dayIn(now, zone)
  const instant = instantOf(day, hour, minute, zone)
  return others
    .filter((other) => other !== zone)
    .map((other) => {
      const otherDay = dayIn(instant, other)
      return { zone: other, clock: clockIn(instant, other), dayShift: Math.sign(Date.parse(otherDay) - Date.parse(day)) }
    })
}

/** The gap between two zones (in minutes) that holds most of the year, and the one at ``now``. */
export function gapBetween(zone: string, other: string, now: Date = new Date()): { usual: number; now: number } {
  const counts = new Map<number, number>()
  const start = Date.UTC(now.getUTCFullYear(), 0, 1)
  for (let week = 0; week < 52; week += 1) {
    const at = new Date(start + week * 7 * 86_400_000)
    const gap = offsetMinutes(zone, at) - offsetMinutes(other, at)
    counts.set(gap, (counts.get(gap) ?? 0) + 1)
  }
  const usual = [...counts.entries()].sort((a, b) => b[1] - a[1])[0][0]
  return { usual, now: offsetMinutes(zone, now) - offsetMinutes(other, now) }
}

/** Every timezone the browser knows, for a field to offer; UTC first. */
export function knownZones(): string[] {
  const listed = typeof Intl.supportedValuesOf === 'function' ? Intl.supportedValuesOf('timeZone') : []
  return ['UTC', ...listed.filter((zone) => zone !== 'UTC')]
}

/** Whether ``zone`` is a timezone the browser knows. */
export function isZone(zone: string): boolean {
  try {
    new Intl.DateTimeFormat('en-US', { timeZone: zone })
    return true
  } catch {
    return false
  }
}
