import { formatDay, formatNumber, formatPercent } from 'ioiti-ui'

import type { BacktestPaper, BacktestPaperEntry, BacktestPaperReport, BacktestRunListed, TaRule } from '@/lib/types'

/**
 * Paper trading, as the pages say it (src/admin/paper.py): each finished
 * weekday, the signals a rule in shadow raised, and those WATA received,
 * traded by a one-day run; and each one's report, one run of its days since
 * it began, made again after each finished day. Pure.
 */

/** The owner of the signals WATA received among the paper runs. */
export const LIVE_OWNER = 'live'

/** The place of a rule (or of the signals received) on the Paper trading page. */
export function paperSectionId(owner: string): string {
  return owner === LIVE_OWNER ? 'live' : `rule-${owner}`
}

/** The Paper trading page, brought to a rule's place (or the signals received's). */
export function paperTradingHref(owner: string): string {
  return `/backtests/paper#${paperSectionId(owner)}`
}

/** The report of a rule (or of the signals received): its newest run, whatever its id tonight. */
export function paperReportHref(owner: string): string {
  return `/backtests/paper/${encodeURIComponent(owner)}`
}

export interface PaperRuleEntry {
  rule: TaRule
  entry: BacktestPaperEntry | undefined
}

/** The rules the page shows: those in shadow first, then any other with days on paper or a report; by name. */
export function paperRules(rules: readonly TaRule[], paper: BacktestPaper | undefined): PaperRuleEntry[] {
  const kept = rules
    .map((rule) => ({ rule, entry: paper?.rules[rule.id] }))
    .filter(({ rule, entry }) => rule.mode === 'shadow' || (entry && (entry.days.length > 0 || entry.report || entry.next_report)))
  return kept.sort((one, other) => Number(other.rule.mode === 'shadow') - Number(one.rule.mode === 'shadow') || one.rule.name.localeCompare(other.rule.name))
}

/** "14 Oct to 19 Oct", or the year with each when it is not this one's. */
export function reportPeriod(report: Pick<BacktestPaperReport, 'from' | 'to'>, year = new Date().getFullYear()): string {
  const withYear = !report.from.startsWith(String(year)) || !report.to.startsWith(String(year))
  return `${formatDay(report.from, withYear)} to ${formatDay(report.to, withYear)}`
}

/** "Since 14 Oct: +1.18 % in 9 trades", what the Signal rules page says of a report. */
export function reportLine(report: BacktestPaperReport): string {
  const headline = report.headline
  const trades = headline?.trades ?? null
  const result = headline?.total_percent === null || headline?.total_percent === undefined ? 'no result' : formatPercent(headline.total_percent, true)
  return `Since ${formatDay(report.from, false)}: ${result}${trades === null ? '' : ` in ${formatNumber(trades)} trade${trades === 1 ? '' : 's'}`}`
}

export interface ReportState {
  text: string
  tone: 'neutral' | 'info' | 'warning'
  /** A report being made (waiting its turn or running): asking for another is refused meanwhile. */
  underWay: boolean
}

/** Where an owner's report stands: made, being made again, one that did not finish, or none yet. */
export function reportState(entry: BacktestPaperEntry | undefined, inShadow: boolean): ReportState {
  const report = entry?.report ?? null
  const next = entry?.next_report ?? null
  if (next && (next.state === 'queued' || next.state === 'running')) {
    const line = next.state === 'queued' ? `waiting its turn${next.position ? `, number ${next.position} in line` : ''}` : 'running'
    return { text: `${report ? 'Being made again' : 'Being made'} to ${formatDay(next.to, false)}: ${line}.`, tone: 'info', underWay: true }
  }
  if (next) {
    const why = next.error ? `: ${next.error}` : ''
    return {
      text: `The report to ${formatDay(next.to, false)} did not finish (${next.state}${why}).${report ? ' The last one made is shown.' : ''} It is made again after the next finished day.`,
      tone: 'warning',
      underWay: false,
    }
  }
  if (report) {
    return {
      text: inShadow ? 'Made again after each finished day, once the market is closed.' : 'No longer made again: the rule left shadow.',
      tone: 'neutral',
      underWay: false,
    }
  }
  return {
    text: inShadow ? 'None yet: the first is made after its first finished weekday, once the market is closed.' : 'None: it was never in shadow since reports were made.',
    tone: 'neutral',
    underWay: false,
  }
}

/** Which versions of its script raised a report's signals, when there were several. */
export function versionsSentence(versions: readonly number[] | undefined): string | null {
  if (!versions || versions.length < 2) return null
  const named = versions.map((version) => `v${version}`)
  return `Its signals were raised by ${named.slice(0, -1).join(', ')} and ${named[named.length - 1]} of the script: the rule changed meanwhile.`
}

/**
 * Why a day on paper made with older backtest code is not to be read beside the report: the version that made it,
 * and, while paper trading still trades its owner (`madeAgain`), that it is made again with today's.
 */
export function staleDayHint(madeWith: string | null | undefined, madeAgain: boolean): string {
  const made = madeWith ? `Made with the backtests of v${madeWith}` : 'Made with older backtests'
  return `${made}: the report is made with today’s.${madeAgain ? ' It is made again once the market is closed.' : ''}`
}

/** Whether any report is being made: the page reads again until it is done. */
export function anyUnderWay(paper: BacktestPaper | undefined): boolean {
  const entries = [...Object.values(paper?.rules ?? {}), ...(paper?.live ? [paper.live] : [])]
  return entries.some((entry) => entry.next_report?.state === 'queued' || entry.next_report?.state === 'running')
}

export interface PaperRunLine {
  text: string
  link: { label: string; href: string }
}

/** What a paper run is, said on its page, and where the rest of its paper trading is; null for any other run. */
export function paperRunLine(run: Pick<BacktestRunListed, 'paper' | 'source' | 'from'>): PaperRunLine | null {
  const paper = run.paper
  if (!paper) return null
  const of = paper.rule === LIVE_OWNER ? 'the signals WATA received' : (run.source.from_rule?.name ?? 'its rule')
  if (paper.report) {
    return {
      text: `Paper trading of ${of}: every day since ${formatDay(run.from, false)}, its account carried from one to the next, made again after each finished day, without random entries.`,
      link: { label: 'Its days on paper', href: paperTradingHref(paper.rule) },
    }
  }
  return {
    text: `One day on paper of ${of}, from an account of its own.`,
    link: { label: 'Its report since it began', href: paperReportHref(paper.rule) },
  }
}

/** The steps of the trail before a paper run: the Paper trading page. */
export function runParents(run: Pick<BacktestRunListed, 'paper'> | undefined): { label: string; href: string }[] {
  return run?.paper ? [{ label: 'Paper trading', href: '/backtests/paper' }] : []
}
