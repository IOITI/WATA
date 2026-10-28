import { describe, expect, it } from 'vitest'

import type { BacktestPaperEntry, BacktestPaperReport, TaRule } from '@/lib/types'
import { listedRun, PAPER_RULE } from '@/test/backtests'

import {
  anyUnderWay,
  paperReportHref,
  paperRules,
  paperRunLine,
  paperSectionId,
  paperTradingHref,
  reportLine,
  reportPeriod,
  reportState,
  runParents,
  versionsSentence,
} from './paperModel'

const REPORT: BacktestPaperReport = {
  run_id: '20261020-213500-aaaaaa', from: '2026-10-14', to: '2026-10-19', made_at: '2026-10-20T21:40:00Z', signals: 12,
  versions: [3], headline: {
    total_percent: 1.18, trades: 9, win_rate: 55.5, worst_drawdown: -2.1, sample: 'few', beaten: null, days_tested: 4,
    profit_loss: 11.8, cells: null,
  },
}

function entry(changes: Partial<BacktestPaperEntry> = {}): BacktestPaperEntry {
  return { days: [], reference: null, report: null, next_report: null, ...changes }
}

function rule(id: string, name: string, mode: TaRule['mode']): TaRule {
  return { id, name, mode } as TaRule
}

describe('where paper trading is', () => {
  it('names each rule\'s place on the page, and its report whatever its run is tonight', () => {
    expect(paperSectionId('a00000000001')).toBe('rule-a00000000001')
    expect(paperSectionId('live')).toBe('live')
    expect(paperTradingHref('a00000000001')).toBe('/backtests/paper#rule-a00000000001')
    expect(paperTradingHref('live')).toBe('/backtests/paper#live')
    expect(paperReportHref('a00000000001')).toBe('/backtests/paper/a00000000001')
  })

  it('lists the rules in shadow first, then those with something on paper, by name', () => {
    const rules = [rule('c', 'Zeta', 'shadow'), rule('d', 'Off, never traded', 'off'), rule('b', 'Live now', 'live'), rule('a', 'Alpha', 'shadow')]
    const paper = { rules: { b: entry({ report: REPORT }) } }

    expect(paperRules(rules, paper).map(({ rule: found }) => found.name)).toEqual(['Alpha', 'Zeta', 'Live now'])
    expect(paperRules(rules, paper)[2].entry?.report).toBe(REPORT)
  })
})

describe('a report', () => {
  it('says its period, and its year when not this one\'s', () => {
    expect(reportPeriod(REPORT, 2026)).toBe('14 Oct to 19 Oct')
    expect(reportPeriod({ from: '2025-12-29', to: '2026-01-05' }, 2026)).toBe('29 Dec 2025 to 05 Jan 2026')
  })

  it('says its result since it began, as the Signal rules page shows it', () => {
    expect(reportLine(REPORT)).toBe('Since 14 Oct: +1.18% in 9 trades')
    expect(reportLine({ ...REPORT, headline: { ...REPORT.headline!, trades: 1, total_percent: -0.5 } })).toBe('Since 14 Oct: −0.50% in 1 trade')
    expect(reportLine({ ...REPORT, headline: null })).toBe('Since 14 Oct: no result')
  })

  it('says where it stands: made, being made, not finished, or none yet', () => {
    expect(reportState(entry({ report: REPORT }), true)).toEqual({ text: 'Made again after each finished day, once the market is closed.', tone: 'neutral', underWay: false })
    expect(reportState(entry({ report: REPORT }), false).text).toBe('No longer made again: the rule left shadow.')
    expect(reportState(undefined, true).text).toBe('None yet: the first is made after its first finished weekday, once the market is closed.')

    const waiting = { run_id: 'x', from: '2026-10-14', to: '2026-10-20', state: 'queued' as const, error: null, position: 3 }
    expect(reportState(entry({ report: REPORT, next_report: waiting }), true)).toEqual({
      text: 'Being made again to 20 Oct: waiting its turn, number 3 in line.', tone: 'info', underWay: true,
    })
    expect(reportState(entry({ next_report: { ...waiting, state: 'running', position: null } }), true).text).toBe('Being made to 20 Oct: running.')

    const failed = { ...waiting, state: 'failed' as const, error: 'The database is unavailable', position: null }
    expect(reportState(entry({ report: REPORT, next_report: failed }), true)).toEqual({
      text: 'The report to 20 Oct did not finish (failed: The database is unavailable). The last one made is shown. It is made again after the next finished day.',
      tone: 'warning',
      underWay: false,
    })
  })

  it('says when several versions of the script raised its signals', () => {
    expect(versionsSentence([3])).toBeNull()
    expect(versionsSentence(undefined)).toBeNull()
    expect(versionsSentence([3, 4, 6])).toBe('Its signals were raised by v3, v4 and v6 of the script: the rule changed meanwhile.')
  })

  it('is being made while any waits its turn or runs', () => {
    const running = { run_id: 'x', from: '2026-10-14', to: '2026-10-20', state: 'running' as const, error: null, position: null }
    expect(anyUnderWay(undefined)).toBe(false)
    expect(anyUnderWay({ rules: { a: entry({ report: REPORT }) } })).toBe(false)
    expect(anyUnderWay({ rules: {}, live: entry({ next_report: running }) })).toBe(true)
    expect(anyUnderWay({ rules: { a: entry({ next_report: { ...running, state: 'failed' } }) } })).toBe(false)
  })
})

describe('a paper run\'s page', () => {
  const ofRule = { source: { kind: 'ta_signals' as const, script: null, from_rule: PAPER_RULE, signals: 12 } }

  it('says what a report is, and leads to its days on paper', () => {
    const line = paperRunLine(listedRun({ ...ofRule, paper: { rule: PAPER_RULE.id, report: true } }))

    expect(line?.text).toBe(
      'Paper trading of Kernel VWAP: every day since 14 Oct, its account carried from one to the next, made again after each finished day, without random entries.',
    )
    expect(line?.link).toEqual({ label: 'Its days on paper', href: '/backtests/paper#rule-a00000000001' })
  })

  it('says what a day is, and leads to the report; the signals received are named so', () => {
    expect(paperRunLine(listedRun({ ...ofRule, paper: { rule: PAPER_RULE.id, day: '2026-10-19' } }))).toEqual({
      text: 'One day on paper of Kernel VWAP, from an account of its own.',
      link: { label: 'Its report since it began', href: '/backtests/paper/a00000000001' },
    })
    const received = listedRun({ source: { kind: 'journal', script: null, from_rule: null, signals: 4 }, paper: { rule: 'live', day: '2026-10-19' } })
    expect(paperRunLine(received)?.text).toBe('One day on paper of the signals WATA received, from an account of its own.')
    expect(paperRunLine(listedRun())).toBeNull()
  })

  it('is under Paper trading in the trail', () => {
    expect(runParents(listedRun({ paper: { rule: 'live', report: true } }))).toEqual([{ label: 'Paper trading', href: '/backtests/paper' }])
    expect(runParents(listedRun())).toEqual([])
    expect(runParents(undefined)).toEqual([])
  })
})
