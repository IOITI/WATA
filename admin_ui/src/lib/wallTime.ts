// The other formats are ioiti-ui's (formatDateTime, formatDay…): this one reads what WATA's server writes.

const EMPTY = '—'

/**
 * A time the server already wrote in the trading timezone, without saying so
 * ("2026-10-12 15:12:00"): shown like the other timestamps (ioiti-ui's
 * formatDateTime), and not converted.
 */
export function formatWallTime(value: string | null | undefined, now: Date = new Date()): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})/.exec(value ?? '')
  if (!match) return value || EMPTY
  const [year, month, day, hour, minute, second] = match.slice(1).map(Number)
  return new Intl.DateTimeFormat('en-GB', {
    timeZone: 'UTC',
    day: '2-digit',
    month: 'short',
    year: year === now.getFullYear() ? undefined : 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(new Date(Date.UTC(year, month - 1, day, hour, minute, second)))
}
