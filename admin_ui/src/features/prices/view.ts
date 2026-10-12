import { allowedIntervals, daysBetween, defaultInterval, shiftDay } from './chartData'
import type { Side } from './chartData'

/**
 * What the Prices page shows, kept in the address so that a view can be
 * bookmarked, reloaded and sent to someone.
 */
export interface PriceView {
  /** null: the first instrument of the period (its index). */
  instrument: number | null
  /** First and last day, of the trading timezone, both included. */
  from: string
  to: string
  /** Length of a bar, in seconds. */
  interval: number
  /**
   * Range bars instead of bars of a fixed length: the price move that ends a
   * bar, or 'auto' for one the server chooses. null: bars of `interval`.
   */
  range: number | 'auto' | null
  side: Side
}

function parseRange(value: string | null): PriceView['range'] {
  if (value === 'auto') return 'auto'
  const size = Number(value)
  return value !== null && value !== '' && Number.isFinite(size) && size > 0 ? size : null
}

function isDay(value: string | null): value is string {
  if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false
  const date = new Date(`${value}T00:00:00Z`)
  // 31 February parses in some browsers, as a day of March.
  return !Number.isNaN(date.getTime()) && date.toISOString().slice(0, 10) === value
}

/** The view an address asks for. Anything missing or senseless falls back to today's, never to an error. */
export function parseView(params: URLSearchParams, today: string): PriceView {
  let from = params.get('from')
  let to = params.get('to')
  if (!isDay(from) || !isDay(to) || from > to) {
    from = today
    to = today
  }
  const days = daysBetween(from, to)
  const interval = Number(params.get('interval'))
  const instrument = Number(params.get('instrument'))
  return {
    instrument: Number.isInteger(instrument) && instrument > 0 ? instrument : null,
    from,
    to,
    interval: allowedIntervals(days).includes(interval) ? interval : defaultInterval(days),
    range: parseRange(params.get('range')),
    side: params.get('side') === 'ask' ? 'ask' : 'bid',
  }
}

/** The address of a view. What is the default is left out: "/prices" alone is today. */
export function viewParams(view: PriceView, today: string): URLSearchParams {
  const params = new URLSearchParams()
  if (view.instrument !== null) params.set('instrument', String(view.instrument))
  if (view.from !== today || view.to !== today) {
    params.set('from', view.from)
    params.set('to', view.to)
  }
  if (view.interval !== defaultInterval(daysBetween(view.from, view.to))) params.set('interval', String(view.interval))
  // As a price, not in ticks: the same address then means the same bars whatever the tick turns out to be.
  if (view.range !== null) params.set('range', String(view.range))
  if (view.side !== 'bid') params.set('side', view.side)
  return params
}

/** Another period for a view: its bars stay as they were when the new period can be shown with them. */
export function withPeriod(view: PriceView, from: string, to: string): PriceView {
  const days = daysBetween(from, to)
  return {
    ...view,
    from,
    to,
    interval: allowedIntervals(days).includes(view.interval) ? view.interval : defaultInterval(days),
  }
}

export interface PeriodPreset {
  label: string
  from: string
  to: string
}

export function periodPresets(today: string): PeriodPreset[] {
  const yesterday = shiftDay(today, -1)
  return [
    { label: 'Today', from: today, to: today },
    { label: 'Yesterday', from: yesterday, to: yesterday },
    { label: '7 days', from: shiftDay(today, -6), to: today },
  ]
}
