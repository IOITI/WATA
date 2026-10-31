// A run checked against funded-account challenges with LuxAlgo's prop-firm simulator (src/admin/propfirm_check.py,
// GET and POST /api/backtests/{id}/propfirms): what leaves for LuxAlgo, said before the click, the request the form
// becomes, and the answer as rows, those that meet the bar first in LuxAlgo's order: a screen, not a ranking. Pure.

import { formatDateTime, formatMoney, formatNumber, formatPercent } from 'ioiti-ui'

import type { PropFirmAnswer, PropFirmAsked, PropFirmChallenge, PropFirmFigures, PropFirmKept } from '@/lib/types'

/** The risk per trade asked, in % of the balance (propfirm_check.RISK_PERCENT, DEFAULT_RISK_PERCENT). */
export const RISK_PERCENT = { min: 0.1, max: 5, default: 0.5 }

export const PRODUCT_TYPES: { value: PropFirmAsked['product_type']; label: string }[] = [
  { value: 'futures', label: 'Futures' },
  { value: 'cfd', label: 'CFDs' },
]

/** The accounts' sizes asked: LuxAlgo screens 40 challenges at most, and a scope keeps a check under it. */
export const ACCOUNT_SCOPES = [
  { value: 'small', label: 'Up to 25,000', min: null, max: 25_000 },
  { value: 'medium', label: '25,000 to 100,000', min: 25_000, max: 100_000 },
  { value: 'large', label: '100,000 and more', min: 100_000, max: null },
  { value: 'any', label: 'Any size', min: null, max: null },
] as const

export type AccountScope = (typeof ACCOUNT_SCOPES)[number]['value']

/** The request the form becomes, or why it cannot be sent. */
export function askedOf(risk: string, productType: PropFirmAsked['product_type'], scope: AccountScope): PropFirmAsked | string {
  const value = Number(risk)
  if (!risk.trim() || !Number.isFinite(value)) return 'Say the risk per trade, in % of the balance'
  if (value < RISK_PERCENT.min || value > RISK_PERCENT.max) {
    return `The risk per trade is from ${RISK_PERCENT.min} % to ${RISK_PERCENT.max} % of the balance`
  }
  const sizes = ACCOUNT_SCOPES.find((entry) => entry.value === scope) ?? ACCOUNT_SCOPES[1]
  return { risk_percent: value, product_type: productType, account_min: sizes.min, account_max: sizes.max }
}

const stopOf = (figures: Pick<PropFirmFigures, 'stoploss_percent'>) => formatPercent(Math.abs(figures.stoploss_percent), false, 1)

/** The run's trades as the simulator reads them, in words. */
export function figuresSaid(figures: Omit<PropFirmFigures, 'r_series'>): string {
  const days = `${figures.days} day${figures.days === 1 ? '' : 's'}`
  const wins = figures.average_win_r === null ? '' : `, winners ${formatNumber(figures.average_win_r, 2)} R`
  const losses = figures.average_loss_r === null ? '' : `${wins ? ' and' : ','} losers ${formatNumber(figures.average_loss_r, 2)} R`
  return `${figures.trades} trades on ${days}, ${formatNumber(figures.trades_per_day, 1)} a day; `
    + `${formatPercent(figures.win_rate * 100, false, 0)} won${wins}${losses} on average (1 R: the stop-loss of ${stopOf(figures)}).`
}

/** What leaves for LuxAlgo when the run is checked, said before the click. */
export function sentSaid(figures: Omit<PropFirmFigures, 'r_series'>): string {
  return `What leaves for LuxAlgo: this run's ${figures.trades} results as R-multiples (each over its stop-loss of `
    + `${stopOf(figures)}), in the order they closed, ${formatNumber(figures.trades_per_day, 1)} trades a day, and the risk `
    + 'and account sizes asked here. Nothing that names the run, the account or its value.'
}

/** The bar the answer was read against. */
export function barSaid(kept: PropFirmKept): string {
  const bar = kept.answer.bar?.minPassPerAttempt ?? kept.bar
  const ev = kept.answer.bar?.requirePositiveEv ? ', and a positive expected value' : ''
  return `The bar: a pass per attempt of ${formatPercent(bar * 100, false, 0)} or more${ev}.`
}

/** What the kept check asked, and when. */
export function askedSaid(kept: PropFirmKept, timezone: string): string {
  const { asked } = kept
  const product = PRODUCT_TYPES.find((entry) => entry.value === asked.productType)?.label.toLowerCase() ?? asked.productType
  const min = asked.accountSizeMin
  const max = asked.accountSizeMax
  const sizes = min && max ? `${formatNumber(min)} to ${formatNumber(max)}` : max ? `up to ${formatNumber(max)}` : min
    ? `${formatNumber(min)} and more` : 'of any size'
  return `Checked ${formatDateTime(kept.asked_at, timezone)}, risking ${formatNumber(asked.riskValue, 2)} % of the balance a trade, `
    + `on ${product} accounts ${sizes}.`
}

export interface ChallengeRow {
  key: string
  name: string
  size: string
  /** Its pass per attempt, and the 95 % range of that estimate. */
  pass: string
  range: string | null
  funded: string
  attempts: string
  cost: string
  ev: string
  evSign: number
  flags: string[]
  meetsBar: boolean
}

const share = (value: number | undefined) => (typeof value === 'number' ? formatPercent(value * 100, false, 0) : '-')

function sizeOf(value: number | undefined, currency: string): string {
  if (typeof value !== 'number') return '-'
  return new Intl.NumberFormat('en-GB', { style: 'currency', currency, maximumFractionDigits: 0 }).format(value)
}

function rowOf(entry: PropFirmChallenge, meets: boolean): ChallengeRow {
  const currency = (entry.currency || 'USD').toUpperCase()
  const low = entry.passCiLow
  const high = entry.passCiHigh
  return {
    key: `${entry.firmId ?? entry.firmName ?? ''}/${entry.challengeId ?? entry.name}`,
    name: entry.name,
    size: sizeOf(entry.accountSize, currency),
    pass: share(entry.passPerAttempt),
    range: typeof low === 'number' && typeof high === 'number' ? `${share(low)} to ${share(high)}` : null,
    funded: share(entry.fundedProbability),
    attempts: typeof entry.expectedAttempts === 'number' ? formatNumber(entry.expectedAttempts, 1) : '-',
    cost: typeof entry.expectedCost === 'number' ? formatMoney(entry.expectedCost, currency) : '-',
    ev: typeof entry.evTotal === 'number' ? formatMoney(entry.evTotal, currency, true) : '-',
    evSign: Math.sign(entry.evTotal ?? 0),
    flags: entry.flagIds ?? [],
    meetsBar: entry.meetsBar ?? meets,
  }
}

/** Each challenge screened: those that meet the bar, then those under it, each in LuxAlgo's order. */
export function challengeRows(answer: PropFirmAnswer): ChallengeRow[] {
  return [...(answer.passing ?? []).map((entry) => rowOf(entry, true)), ...(answer.belowBar ?? []).map((entry) => rowOf(entry, false))]
}

/** What the answer says beside its rows: the challenges left out, the count that meets the bar. */
export function answerSaid(answer: PropFirmAnswer): string {
  const meeting = answer.passing?.length ?? 0
  const screened = meeting + (answer.belowBar?.length ?? 0)
  const left = answer.excludedNotSimulatable
    ? ` ${answer.excludedNotSimulatable} challenge${answer.excludedNotSimulatable === 1 ? '' : 's'} LuxAlgo cannot simulate left out.`
    : ''
  if (!screened) return `No challenge was screened.${left}`
  // A pass is not a profit: an instant account passes most of the time and still costs more than it pays.
  const losing = (answer.passing ?? []).filter((entry) => typeof entry.evTotal === 'number' && entry.evTotal < 0).length
  const losingSaid = losing ? `, ${losing === meeting && meeting > 1 ? 'all' : losing} of them at a negative expected value` : ''
  return `${meeting} of ${screened} challenge${screened === 1 ? '' : 's'} screened meet${meeting === 1 ? 's' : ''} the bar${losingSaid}.${left}`
}
