// The messages between the Technical Analysis pages and their Pine worker.
//
// The worker can open no connection (its Content-Security-Policy): the bars it
// runs a script on come with the request, and those a script asks for through
// request.security are asked of the page, which reads them from WATA.

import type { IPineInput } from 'pinets'

/** A bar as PineTS takes it: open and close times in epoch milliseconds. */
export interface Candle {
  openTime: number
  closeTime: number
  open: number
  high: number
  low: number
  close: number
  volume: number
}

export interface RunRequest {
  id: number
  op: 'run'
  /** The prepared source (prepare.ts): native Pine, libraries inlined. */
  source: string
  /** The chart's bars, oldest first. */
  bars: Candle[]
  /** The chart's ticker and timeframe, as request.security and syminfo see them. */
  symbol: string
  timeframe: string
  inputs?: Record<string, unknown>
}

export type WorkerRequest = { id: number; op: 'compile'; source: string } | RunRequest

/** The page's answer to a SeriesQuestion. */
export interface SeriesAnswer {
  op: 'series'
  fetchId: number
  bars?: Candle[]
  error?: string
}

/** The worker asks the page for the bars of another symbol or timeframe (request.security). */
export interface SeriesQuestion {
  op: 'fetch'
  fetchId: number
  /** The run it is for. */
  id: number
  symbol: string
  timeframe: string
  from?: number
  to?: number
  limit?: number
}

/** Where a compile or run failed, as far as PineTS's message says. */
export interface PineFailure {
  message: string
  line: number | null
  column: number | null
}

export interface PlotSummary {
  title: string
  points: number
  last: number | boolean | string | null
}

export interface AlertEvent {
  type: string
  title: string | null
  message: string
  barIndex: number
  time: number
}

export interface RunSummary {
  milliseconds: number
  bars: number
  plots: PlotSummary[]
  alerts: AlertEvent[]
  warnings: { message: string; bar: number }[]
}

export type WorkerResponse =
  | {
      id: number
      ok: true
      op: 'compile'
      declaration: 'indicator' | 'strategy' | null
      inputs: IPineInput[]
    }
  | ({ id: number; ok: true; op: 'run' } & RunSummary)
  | { id: number; ok: false; error: PineFailure }

export type FromWorker = WorkerResponse | SeriesQuestion
export type ToWorker = WorkerRequest | SeriesAnswer
