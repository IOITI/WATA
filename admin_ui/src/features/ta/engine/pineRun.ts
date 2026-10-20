// What a PineTS compile or run becomes for the pages: pure, so that the worker,
// the server's runner and the tests share it.

import type { AlertEvent, PineFailure, PlotSummary, RunSummary } from './workerProtocol'

// PineTS puts the position only in the text: "... Unexpected token (12:5)" from its
// JavaScript parser, "... at 12:5" from its Pine parser.
const POSITION = /(?:\bat\s+|\()(\d+):(\d+)\)?\s*$/

export function compileFailure(error: unknown): PineFailure {
  const message = error instanceof Error ? error.message : String(error)
  const match = POSITION.exec(message.trim())
  return {
    message,
    line: match ? Number(match[1]) : null,
    column: match ? Number(match[2]) : null,
  }
}

interface PlotPoint {
  value?: unknown
}

interface PlotLike {
  title?: string
  data?: PlotPoint[]
}

interface AlertLike {
  type: string
  title?: unknown
  message: string
  bar_index: number
  time: number
}

interface ContextLike {
  plots?: Record<string, PlotLike>
  alerts?: AlertLike[]
  warnings?: { message: string; bar: number }[]
  marketData?: unknown[]
}

/**
 * An alert's title and message. PineTS 0.11.1 hands the named arguments of
 * alertcondition(cond, title = …, message = …) over as one object in place of the
 * title, the message left empty: they are taken back from it.
 */
export function alertParts(alert: Pick<AlertLike, 'title' | 'message'>): { title: string | null; message: string } {
  const raw = alert.title
  if (raw !== null && typeof raw === 'object') {
    const named = raw as { title?: unknown; message?: unknown }
    return {
      title: typeof named.title === 'string' ? named.title : null,
      message: typeof named.message === 'string' ? named.message : alert.message,
    }
  }
  return { title: typeof raw === 'string' ? raw : null, message: alert.message ?? '' }
}

function plotValue(value: unknown): PlotSummary['last'] {
  if (typeof value === 'number') return Number.isFinite(value) ? value : null
  if (typeof value === 'boolean' || typeof value === 'string') return value
  return null
}

/** The last value of each plot that is not na, and the alerts in bar order. */
export function runSummary(context: ContextLike, milliseconds: number): RunSummary {
  const plots: PlotSummary[] = Object.entries(context.plots ?? {}).map(([key, plot]) => {
    const points = plot.data ?? []
    let last: PlotSummary['last'] = null
    for (let index = points.length - 1; index >= 0 && last === null; index -= 1) {
      last = plotValue(points[index]?.value)
    }
    return { title: plot.title ?? key, points: points.length, last }
  })
  const alerts: AlertEvent[] = (context.alerts ?? [])
    .map((alert) => ({
      type: alert.type,
      ...alertParts(alert),
      barIndex: alert.bar_index,
      time: alert.time,
    }))
    .sort((first, second) => first.time - second.time)
  return {
    milliseconds: Math.round(milliseconds),
    bars: context.marketData?.length ?? 0,
    plots,
    alerts,
    warnings: (context.warnings ?? []).map(({ message, bar }) => ({ message, bar })),
  }
}
