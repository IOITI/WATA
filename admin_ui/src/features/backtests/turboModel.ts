import { formatDay, formatNumber } from '@/lib/format'
import type { BacktestTurboDirection, BacktestTurboMeasure, BacktestTurboModel } from '@/lib/types'

import { PROVIDER_LABEL } from './model'

/** What the pages say of the turbos made up from an index (src/backtest/turbo_model.py). Pure. */

export function underlyingLabel(underlying: Pick<BacktestTurboMeasure['underlying'], 'provider' | 'key'>): string {
  return `${PROVIDER_LABEL[underlying.provider] ?? underlying.provider} ${underlying.key}`
}

/** "dukascopy/us100" as the pages name it: "Dukascopy us100". */
export function underlyingOfRef(ref: string): string {
  const [provider, ...key] = ref.split('/')
  return underlyingLabel({ provider: provider as BacktestTurboMeasure['underlying']['provider'], key: key.join('/') })
}

function direction(name: string, measured: BacktestTurboDirection): string {
  const error = measured.error_percent === null ? '' : `, ${formatNumber(measured.error_percent, 1)} % median error`
  return `${name} ${formatNumber(measured.factor, 4)} € a point, levels every ${formatNumber(measured.spacing, 0)} points${error}`
}

/** A measure in a sentence: what it was made on, and how well the model prices the recorded turbos. */
export function measureSentence(measure: BacktestTurboMeasure | null | undefined): string {
  if (!measure) return 'Not measured yet: the Backtests page measures them on the days the price recorder kept turbos.'
  const days = `${measure.days} recorded day${measure.days === 1 ? '' : 's'}`
  const period = measure.first && measure.last ? ` (${formatDay(measure.first, false)} to ${formatDay(measure.last)})` : ''
  return (
    `Measured on ${days}${period} against ${underlyingLabel(measure.underlying)}: ` +
    `${direction('long', measure.directions.long)}; ${direction('short', measure.directions.short)}.` +
    (measure.few_days ? ' Few days: take what they give as a rough guess.' : '')
  )
}

export function measureState(model: BacktestTurboModel): string | null {
  if (model.state === 'running') {
    return model.asked ? `Measuring the turbos of ${formatDay(model.asked.from, false)} to ${formatDay(model.asked.to)}…` : 'Measuring…'
  }
  if (model.state === 'failed') return `The last measure failed: ${model.error ?? 'no reason given'}`
  if (model.state === 'interrupted') return 'The last measure was interrupted when the admin stopped.'
  return null
}

/** The period measured by default: the month before today, which the recorder's retention keeps. */
export function defaultMeasurePeriod(today: string, days = 30): { from: string; to: string } {
  const end = new Date(`${today}T12:00:00Z`)
  const start = new Date(end.getTime() - (days - 1) * 86_400_000)
  return { from: start.toISOString().slice(0, 10), to: today }
}
