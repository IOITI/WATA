import { allowedIntervals, daysBetween, defaultInterval, shiftDay } from './chartData'
import type { Side } from './chartData'

/**
 * What an asset's chart shows, kept in the address so that a view can be
 * bookmarked, reloaded and sent to someone. The asset is in the path. What
 * the address leaves out is the default, whose day is the asset's last one
 * with data rather than today: a turbo is quoted for a day, and an ended one
 * has nothing today.
 */
export interface ChartView {
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

/** The parameters of the address a view is kept in, and nothing else of it. */
export const VIEW_PARAMS = ['from', 'to', 'interval', 'range', 'side'] as const

function parseRange(value: string | null): ChartView['range'] {
  if (value === 'auto') return 'auto'
  const size = Number(value)
  return value !== null && value !== '' && Number.isFinite(size) && size > 0 ? size : null
}

export function isDay(value: string | null): value is string {
  if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false
  const date = new Date(`${value}T00:00:00Z`)
  // 31 February parses in some browsers, as a day of March.
  return !Number.isNaN(date.getTime()) && date.toISOString().slice(0, 10) === value
}

/** The view an address asks for. Anything missing or senseless falls back to the default day's, never to an error. */
export function parseView(params: URLSearchParams, defaultDay: string): ChartView {
  let from = params.get('from')
  let to = params.get('to')
  if (!isDay(from) || !isDay(to) || from > to) {
    from = defaultDay
    to = defaultDay
  }
  const days = daysBetween(from, to)
  const interval = Number(params.get('interval'))
  return {
    from,
    to,
    interval: allowedIntervals(days).includes(interval) ? interval : defaultInterval(days),
    range: parseRange(params.get('range')),
    side: params.get('side') === 'ask' ? 'ask' : 'bid',
  }
}

/** The address of a view. What is the default is left out: no parameter at all is the default day. */
export function viewParams(view: ChartView, defaultDay: string): URLSearchParams {
  const params = new URLSearchParams()
  if (view.from !== defaultDay || view.to !== defaultDay) {
    params.set('from', view.from)
    params.set('to', view.to)
  }
  if (view.interval !== defaultInterval(daysBetween(view.from, view.to))) params.set('interval', String(view.interval))
  // As a price, not in ticks: the same address then means the same bars whatever the tick turns out to be.
  if (view.range !== null) params.set('range', String(view.range))
  if (view.side !== 'bid') params.set('side', view.side)
  return params
}

/** The default view of a day: ten-second bars of the bid. */
export function dayView(day: string): ChartView {
  return { from: day, to: day, interval: defaultInterval(1), range: null, side: 'bid' }
}

/** Another period for a view: its bars stay as they were when the new period can be shown with them. */
export function withPeriod(view: ChartView, from: string, to: string): ChartView {
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

/**
 * The periods offered in one click, counted back from ``anchor``: today for
 * an asset still recorded, else its last day with data.
 */
export function periodPresets(anchor: string, today: string): PeriodPreset[] {
  const before = shiftDay(anchor, -1)
  const live = anchor === today
  return [
    { label: live ? 'Today' : 'Last day', from: anchor, to: anchor },
    { label: live ? 'Yesterday' : 'Day before', from: before, to: before },
    { label: '7 days', from: shiftDay(anchor, -6), to: anchor },
    { label: '30 days', from: shiftDay(anchor, -29), to: anchor },
  ]
}
