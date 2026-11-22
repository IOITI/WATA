// What a rule's protections say on its page and in the go-live dialog (src/admin/live_safety.py, through
// GET /api/ta/rules/<id>/protections): where each leads, the line that sums them up, and the figures of what the
// account buys. Pure.

import { formatDay, formatMoney } from 'ioiti-ui'

import { anchorOf, blockAnchorOf } from '@/features/config/catalog'
import type { Protection, ProtectionFunds } from '@/lib/types'

/**
 * Where a protection is set: its key's row on the Configuration page, its block's heading while the rule it belongs
 * to is not in config.json (no row of it then), or the page it leads to.
 */
export function protectionHref(check: Protection): string | null {
  if (!check.link) return null
  if ('href' in check.link) return check.link.href
  const { group, path, block } = check.link.config
  return `/config/${group}#${block ? blockAnchorOf(block) : anchorOf(path)}`
}

const counted = (count: number, one: string, many = `${one}s`) => `${count} ${count === 1 ? one : many}`

/** "2 to see to, 4 off, 4 on": the checks counted by state, what needs seeing to first. */
export function protectionsLine(checks: Protection[]): string {
  const of = (state: Protection['state']) => checks.filter((check) => check.state === state).length
  const parts = [
    of('fail') > 0 ? `${of('fail')} to see to` : null,
    of('open') > 0 ? `${of('open')} off or not known` : null,
    of('pass') > 0 ? `${of('pass')} on` : null,
  ].filter((part): part is string => part !== null)
  return parts.length > 0 ? parts.join(', ') : 'Nothing read'
}

/** The checks that need seeing to, by name: what the dialog says before its list. */
export function toSeeTo(checks: Protection[]): string | null {
  const failing = checks.filter((check) => check.state === 'fail').map((check) => check.label.toLowerCase())
  if (failing.length === 0) return null
  return `To see to before trusting it live: ${failing.join(', ')}.`
}

export interface FundsFigure {
  label: string
  value: string
  hint?: string
}

/** What the account buys, as figures: its value, the dearest turbo an order buys, the entries bought, what all need. */
export function fundsFigures(funds: ProtectionFunds | null | undefined): FundsFigure[] {
  if (!funds || funds.value === null) return []
  const figures: FundsFigure[] = [
    { label: 'Account', value: formatMoney(funds.value), hint: 'Its value at the last daily report.' },
    {
      label: 'Dearest turbo',
      value: formatMoney(funds.dearest_ask),
      hint: `The dearest turbo an order buys at full scale, with ${counted(funds.safety_units, 'safety turbo')} left out of it and ${funds.share_percent} % of the account used.`,
    },
  ]
  if (funds.entries > 0 && funds.affordable !== null) {
    const source = funds.source
    const span = source?.first_day && source.last_day ? `, ${formatDay(source.first_day)} to ${formatDay(source.last_day)}` : ''
    figures.push({
      label: 'Entries it buys',
      value: `${funds.affordable} of ${funds.entries}`,
      hint: `The entries of ${source?.name ?? 'its backtest'}${span}${
        funds.safety_units > 0 && funds.affordable_without_safety !== null
          ? `; ${funds.affordable_without_safety} with no safety turbo`
          : ''
      }.`,
    })
    if (funds.needed_for_all !== null) {
      figures.push({
        label: 'Buys them all from',
        value: formatMoney(funds.needed_for_all),
        hint:
          funds.needed_for_most !== null && funds.needed_for_most < funds.needed_for_all
            ? `${funds.most_percent} % of them from ${formatMoney(funds.needed_for_most)}.`
            : 'The account that buys every one of them.',
      })
    }
  }
  return figures
}
