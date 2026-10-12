import type { DateRange } from '@/components/ui/calendar'

import { toIsoDay } from './format'

export const ALL_TIME: DateRange = { from: null, to: null }

export function lastDays(days: number, today: Date = new Date()): DateRange {
  const from = new Date(today)
  from.setDate(today.getDate() - (days - 1))
  return { from, to: today }
}

/** The range as the API's `from` / `to`. A range still being picked (no end yet) is open-ended. */
export function rangeParams(range: DateRange): { from: string | null; to: string | null } {
  return {
    from: range.from ? toIsoDay(range.from) : null,
    to: range.to ? toIsoDay(range.to) : null,
  }
}
