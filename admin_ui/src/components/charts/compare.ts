/**
 * Things set side by side on one chart (backtest runs): how many at most, and
 * the colour of each.
 */

/** As many as there are colours that stay apart, for colour-blind readers too (styles/theme.css). */
export const MAX_COMPARED = 4

const COMPARE_COLORS = [
  'var(--chart-compare-1)',
  'var(--chart-compare-2)',
  'var(--chart-compare-3)',
  'var(--chart-compare-4)',
]

/** The colour of the nth thing compared. It follows the thing, not its rank on the day. */
export function compareColor(index: number): string {
  return COMPARE_COLORS[index % COMPARE_COLORS.length]
}
