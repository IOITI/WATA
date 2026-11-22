import { describe, expect, it } from 'vitest'

import type { Protection, ProtectionFunds } from '@/lib/types'

import { fundsFigures, protectionHref, protectionsLine, toSeeTo } from './protections'

const check = (id: string, state: Protection['state'], link: Protection['link'] = null): Protection => ({
  id, label: id === 'funds' ? 'What the account buys' : id === 'edge_guard' ? 'Edge guard' : id, state, detail: '…', link,
})

describe('a rule’s protections', () => {
  it('lead to the row of their key on the Configuration page, or to their page', () => {
    const floor = check('account_guard', 'open', {
      config: { group: 'day', path: 'trade.rules[risk_management].rule_config.pause_below_balance_eur' },
    })
    expect(protectionHref(floor)).toBe('/config/day#p-trade-rules-risk-management-rule-config-pause-below-balance-eur')
    expect(protectionHref(check('edge_guard', 'fail', { href: '/backtests/new?rule=f13986be5e9b' }))).toBe('/backtests/new?rule=f13986be5e9b')
    expect(protectionHref(check('telegram', 'pass'))).toBeNull()
    // The window around US releases switched off: no row of its keys, its block's heading.
    const releases = check('release_window', 'open', {
      config: { group: 'hours', path: 'trade.rules[macro_news].rule_config.minutes_before', block: 'news' },
    })
    expect(protectionHref(releases)).toBe('/config/hours#b-news')
  })

  it('are counted by state, what needs seeing to first, and named when they do', () => {
    const checks = [check('funds', 'fail'), check('edge_guard', 'fail'), check('breakers', 'open'), check('telegram', 'pass')]
    expect(protectionsLine(checks)).toBe('2 to see to, 1 off or not known, 1 on')
    expect(toSeeTo(checks)).toBe('To see to before trusting it live: what the account buys, edge guard.')
    expect(toSeeTo([check('telegram', 'pass')])).toBeNull()
    expect(protectionsLine([])).toBe('Nothing read')
  })
})

describe('what the account buys', () => {
  const FUNDS: ProtectionFunds = {
    value: 21.82, share_percent: 100, safety_units: 1, dearest_ask: 10.91, entries: 5, affordable: 2, affordable_without_safety: 5,
    needed_for_all: 28.6, needed_for_most: 28.6, most_percent: 90, dearest_entry_ask: 14.3,
    source: { kind: 'backtest', run_id: '20261120-101010-c18c49', name: 'Kernel VWAP (again)', first_day: '2026-10-13', last_day: '2026-11-20' },
  }

  it('says the account, the dearest turbo it buys, the entries it buys and what buys them all', () => {
    expect(fundsFigures(FUNDS)).toEqual([
      { label: 'Account', value: '€21.82', hint: 'Its value at the last daily report.' },
      { label: 'Dearest turbo', value: '€10.91', hint: 'The dearest turbo an order buys at full scale, with 1 safety turbo left out of it and 100 % of the account used.' },
      { label: 'Entries it buys', value: '2 of 5', hint: 'The entries of Kernel VWAP (again), 13 Oct 2026 to 20 Nov 2026; 5 with no safety turbo.' },
      { label: 'Buys them all from', value: '€28.60', hint: 'The account that buys every one of them.' },
    ])
  })

  it('says from what 90 % of them are bought when it is less than all of them', () => {
    expect(fundsFigures({ ...FUNDS, needed_for_most: 21.9 }).at(-1)).toEqual({ label: 'Buys them all from', value: '€28.60', hint: '90 % of them from €21.90.' })
  })

  it('says the account alone without entries, and nothing without its value', () => {
    expect(fundsFigures({ ...FUNDS, entries: 0, affordable: 0, source: null }).map((figure) => figure.label)).toEqual(['Account', 'Dearest turbo'])
    expect(fundsFigures({ ...FUNDS, value: null })).toEqual([])
    expect(fundsFigures(null)).toEqual([])
  })
})
