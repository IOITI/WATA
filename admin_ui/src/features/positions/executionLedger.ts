import type { ExecutionLedger, LedgerFigures, LedgerGroup } from '@/lib/types'

/**
 * What the Orders page says of the execution ledger (src/admin/execution_ledger.py): what the real trades lost
 * between their signal and their fills. Costs are points of a trade's result, a cost positive.
 */

export type Tone = 'negative' | 'positive' | 'neutral'

export interface LedgerTile {
  key: string
  label: string
  value: string
  hint: string
  tone: Tone
}

/** A cost in points of a result: "+0.42 pt", "−0.10 pt", "—" when none was measured. */
export function pointsText(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return '—'
  const rounded = Math.round(value * 100) / 100
  const sign = rounded > 0 ? '+' : rounded < 0 ? '−' : ''
  return `${sign}${Math.abs(rounded).toFixed(2)} pt`
}

/** A time in seconds: "0.25 s", "14 s", "2 min 5 s", "—". */
export function secondsText(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return '—'
  const sign = value < 0 ? '−' : ''
  const seconds = Math.abs(value)
  if (seconds < 10) return `${sign}${seconds.toFixed(seconds < 1 ? 2 : 1)} s`
  if (seconds < 120) return `${sign}${Math.round(seconds)} s`
  const minutes = Math.floor(seconds / 60)
  return `${sign}${minutes} min ${Math.round(seconds - 60 * minutes)} s`
}

/** A cost above a tenth of a point is said as one; below minus a tenth, as a gain. */
export function costTone(value: number | null | undefined): Tone {
  if (value == null || !Number.isFinite(value)) return 'neutral'
  if (value > 0.1) return 'negative'
  if (value < -0.1) return 'positive'
  return 'neutral'
}

function measured(figures: LedgerFigures | undefined): string {
  if (!figures || figures.count === 0) return 'Not measured yet'
  return `Typical of ${figures.count} trade${figures.count === 1 ? '' : 's'}; mean ${pointsText(figures.mean)}, 1 in 10 above ${pointsText(figures.p90)}`
}

/** The four tiles: what a trade lost in all, and to each of its three causes. */
export function ledgerTiles(ledger: ExecutionLedger): LedgerTile[] {
  const { summary } = ledger
  const cost = summary.cost_percent
  return [
    {
      key: 'cost',
      label: 'Lost to execution',
      value: pointsText(cost.median),
      hint:
        cost.count === 0
          ? 'Not measured yet'
          : `Per trade, typical. ${pointsText(cost.sum)} over ${cost.count} trade${cost.count === 1 ? '' : 's'}, mean ${pointsText(cost.mean)}`,
      tone: costTone(cost.median),
    },
    {
      key: 'delay',
      label: 'Signal to order',
      value: pointsText(summary.delay_percent.median),
      hint:
        summary.delay_percent.count === 0
          ? 'The turbo’s recorded ask from the signal to the order: not measured yet'
          : `The turbo dearer when the order left than when the signal was raised. ${measured(summary.delay_percent)}`,
      tone: costTone(summary.delay_percent.median),
    },
    {
      key: 'entry',
      label: 'Entry slippage',
      value: pointsText(summary.entry_slippage_percent.median),
      hint:
        summary.entry_slippage_percent.count === 0
          ? 'Paid above the ask the order was sized on: not measured yet'
          : `Paid above the ask the order was sized on. ${measured(summary.entry_slippage_percent)}`,
      tone: costTone(summary.entry_slippage_percent.median),
    },
    {
      key: 'exit',
      label: 'Exit slippage',
      value: pointsText(summary.exit_slippage_percent.median),
      hint:
        summary.exit_slippage_percent.count === 0
          ? 'Sold under the price the sale was decided on: not measured yet'
          : `Sold under the price the sale was decided on. ${measured(summary.exit_slippage_percent)}`,
      tone: costTone(summary.exit_slippage_percent.median),
    },
  ]
}

export interface LedgerLine {
  group: string
  trades: number
  result: string
  cost: string
  costTone: Tone
  exit: string
  exitTone: Tone
}

/** The trades by how they ended: each group's typical result, cost and exit slippage. */
export function ledgerLines(groups: LedgerGroup[]): LedgerLine[] {
  return groups.map((group) => ({
    group: group.group,
    trades: group.trades,
    result: pointsText(group.result_percent.median).replace(' pt', ' %'),
    cost: pointsText(group.cost_percent.median),
    costTone: costTone(group.cost_percent.median),
    exit: pointsText(group.exit_slippage_percent.median),
    exitTone: costTone(group.exit_slippage_percent.median),
  }))
}

/** How long signals took to reach WATA, by where they were raised: "TradingView 14 s, WATA's engine 1.0 s". */
export function deliveryText(groups: LedgerGroup[]): string | null {
  const said = groups
    .filter((group) => group.signal_to_received.count > 0)
    .map((group) => `${group.group} ${secondsText(group.signal_to_received.median)}`)
  return said.length ? said.join(', ') : null
}

/** Saxo's side, typical: its acceptance of a purchase and the fill after it, then a sale's attempts and fill. */
export function saxoText(ledger: ExecutionLedger): string {
  const { summary } = ledger
  return [
    `Saxo accepted a purchase in ${secondsText(summary.order_to_accepted.median)}`,
    `filled it ${secondsText(summary.accepted_to_fill.median)} later by its own clock`,
    `a sale was sent ${secondsText(summary.sale_attempts.median)} after it was first tried`,
    `and filled ${secondsText(summary.sale_to_fill.median)} after it left`,
  ].join(', ')
}
