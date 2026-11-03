// The rule check of a script, said: whether it can become a signal rule as it is, its
// findings in three parts (repainting, its alerts, what WATA serves otherwise) and what
// the run test on recorded bars found. Each finding's own sentence is written where it
// is found (engine/ruleCheck.ts for what its source says, src/research/rule_check.py for
// the run test); here, the verdict, the checks a CheckList shows, the badges. Pure.

import type { Check } from 'ioiti-ui'

import type {
  FindingLevel,
  RuleCheckReport,
  RuleChecksState,
  RuleCheckSummary,
  RuleFinding,
  RuleTested,
  RuleTestSettings,
  RuleVerdict,
} from '@/lib/types'

import type { RuleCheck } from '../engine/ruleCheck'
import { barsLabel } from '../signals/ruleModel'

export const VERDICT_LABEL: Record<RuleVerdict, string> = {
  ready: 'Ready for a rule',
  to_change: 'To adapt',
  may_repaint: 'May repaint',
  repaints: 'Repaints',
  needs_alerts: 'Needs alerts',
  blocked: 'Cannot be a rule',
}

export const VERDICT_TONE: Record<RuleVerdict, 'success' | 'warning' | 'danger' | 'neutral'> = {
  ready: 'success',
  to_change: 'warning',
  may_repaint: 'warning',
  repaints: 'danger',
  needs_alerts: 'warning',
  blocked: 'danger',
}

/** The parts a report is said in, each finding in the one its area names. */
export const AREAS = [
  { id: 'repaint', label: 'Repainting', said: 'Are its signals the same live and in a backtest?' },
  { id: 'alerts', label: 'Its alerts', said: 'What a rule would turn into signals.' },
  { id: 'wata', label: 'On WATA', said: 'What WATA serves otherwise than TradingView, and what a run costs.' },
] as const
export type AreaId = (typeof AREAS)[number]['id']

/** What each finding is about, in a few words: its check's label. */
const FINDING_LABEL: Record<string, string> = {
  'bar-state': 'Where a run ends',
  'bar-end': 'The last bars',
  clock: 'The clock',
  random: 'Chance',
  'visible-range': "The chart's visible range",
  'other-series': 'Another series',
  lookahead: 'Lookahead',
  'strategy-state': "The strategy's own state",
  'repaints-at-the-bar': 'Live and a backtest differ',
  'repaints-earlier': 'Its past alerts change',
  'no-alerts': 'No alert',
  'alert-messages': 'Messages that make no signal',
  strategy: 'A strategy',
  'title-expression': 'A title not written as text',
  'title-duplicate': 'Alerts sharing a title',
  'message-plot': 'A plot its message names',
  'fires-every-bar': 'An alert at every bar',
  'never-fires': 'An alert that never fired',
  ticker: 'A series WATA has not',
  'live-ticker': 'A series read only in backtests',
  'no-data': 'Data WATA has not',
  mintick: 'syminfo.mintick',
  syminfo: 'What syminfo holds',
  'utc-time': 'Time without a zone',
  'indicator-timeframe': 'Its own timeframe',
  slow: 'What a run costs',
  'fails-on-bars': "WATA's bars",
  version: 'Its Pine version',
  library: 'A library',
  'library-missing': 'A library WATA has not',
}

export function findingLabel(finding: Pick<RuleFinding, 'id'>): string {
  return FINDING_LABEL[finding.id] ?? finding.id
}

/** Where a finding is first, in words: “Line 3”, “In the library Gate, line 3”. */
export function placeSaid(finding: Pick<RuleFinding, 'places' | 'count'>): string | null {
  const first = finding.places[0]
  if (!first) return null
  const more = finding.count > 1 ? ` (and ${finding.count - 1} more)` : ''
  return first.file ? `In the library ${first.file}, line ${first.line}${more}` : `Line ${first.line}${more}`
}

/** A finding as a check: what it found, where, and what to change. */
export function checkOf(finding: RuleFinding): Check & { finding: RuleFinding } {
  const place = placeSaid(finding)
  const detail = [place ? `${place}: ${finding.message}` : finding.message, finding.fix].filter(Boolean).join(' ')
  return { id: finding.id, label: findingLabel(finding), state: finding.level === 'note' ? 'pass' : 'fail', detail, finding }
}

function plural(count: number, one: string, many = `${one}s`): string {
  return `${count.toLocaleString('en-GB')} ${count === 1 ? one : many}`
}

/** What a report found, in its parts: the findings to act on as failed checks, and what passed or is not known yet. */
export function sectionsOf(report: Pick<RuleCheckReport, 'findings' | 'tested' | 'alerts' | 'alert_calls'>) {
  const acting = report.findings.filter((finding) => finding.level !== 'note')
  const tested = report.tested !== null && !report.tested.failure
  const same = report.findings.find((finding) => finding.id === 'same-live-and-backtest')
  return AREAS.map((area) => {
    const checks: (Check & { finding?: RuleFinding })[] = acting.filter((finding) => finding.area === area.id).map(checkOf)
    if (checks.length === 0) {
      if (area.id === 'repaint') {
        checks.push(
          tested && same
            ? { id: 'same', label: 'The same live and in a backtest', state: 'pass', detail: same.message }
            : {
                id: 'untested',
                label: 'Nothing that reaches its alerts repaints',
                state: tested ? 'pass' : 'open',
                detail: tested
                  ? 'Nothing it reads gives otherwise at the last bar of a run.'
                  : 'As its source reads; a run test on recorded bars sets each day as a backtest has it against what live had.',
              },
        )
      } else if (area.id === 'alerts') {
        const titles = report.alerts.map((alert) => alert.title).filter((title): title is string => title !== null)
        checks.push({
          id: 'alerts',
          label: titles.length > 0 ? plural(titles.length, 'alertcondition') : plural(report.alert_calls.length, 'alert() call'),
          state: 'pass',
          detail:
            titles.length > 0
              ? `${titles.map((title) => `“${title}”`).join(', ')}: a rule maps each title to long, short, close-position, a message, or nothing.`
              : 'Its messages are WATA\'s JSON: a rule reads them as signals.',
        })
      } else {
        checks.push({
          id: 'runs',
          label: tested ? 'Runs on WATA' : 'Not run on recorded bars yet',
          state: tested ? 'pass' : 'open',
          detail: tested ? `It ran on ${report.tested?.spoken ?? 'the days tested'}.` : 'A run test runs it on the days WATA recorded.',
        })
      }
    }
    return { ...area, checks }
  })
}

/** What is worth knowing and asks nothing: the notes, but the ones a section says already. */
export function notesOf(report: Pick<RuleCheckReport, 'findings'>): RuleFinding[] {
  return report.findings.filter((finding) => finding.level === 'note' && finding.id !== 'same-live-and-backtest')
}

/** The verdict of a report in a sentence. */
export function verdictSaid(report: Pick<RuleCheckReport, 'verdict' | 'findings' | 'tested' | 'candidates'>): string {
  const changes = report.findings.filter((finding) => finding.level === 'change').length
  const tested = report.tested !== null && !report.tested.failure
  switch (report.verdict) {
    case 'ready':
      return tested
        ? 'It can be a signal rule as it is: its alerts were the same live and in a backtest on the days tested.'
        : 'As its source reads, it can be a signal rule as it is.'
    case 'to_change':
      return `It can be a signal rule once ${plural(changes, 'thing')} below ${changes === 1 ? 'is' : 'are'} changed.`
    case 'may_repaint':
      return report.findings.some((finding) => finding.id === 'no-alert-compared')
        ? 'It may repaint: at the bars its run test ended at, no alert came, live or in a backtest, to compare.'
        : 'It may repaint: a run test on recorded bars says whether its alerts are the same live and in a backtest.'
    case 'repaints':
      return 'It repaints: its alerts are not the same live and in a backtest.'
    case 'needs_alerts':
      return report.candidates.length > 0
        ? `It needs alerts before it can be a signal rule: it draws ${plural(report.candidates.length, 'event')} an alert could be raised on.`
        : 'It needs alerts before it can be a signal rule: nothing in it raises one.'
    case 'blocked':
      return 'It cannot be a signal rule as it is.'
  }
}

/** The verdict as a check's icon: passed, failed, or not known (it may repaint, and no test told). */
export function verdictState(report: Pick<RuleCheckReport, 'verdict'>): Check['state'] {
  if (report.verdict === 'ready') return 'pass'
  return report.verdict === 'may_repaint' ? 'open' : 'fail'
}

const ASSET_LABEL: Record<string, string> = { 'capital/US100': "Capital.com's US100", 'dukascopy/us100': "Dukascopy's US100" }

/** What a run test was made with: “Capital.com's US100, 1 min bars, 1 day of history”. */
export function settingsSaid(settings: RuleTestSettings): string {
  const history = settings.history_days === null ? '' : `, ${plural(settings.history_days, 'day')} of history`
  return `${ASSET_LABEL[settings.asset] ?? settings.asset}, ${barsLabel(settings.bars)} bars${history}`
}

/** What the run test shown was made on: “Tested on 13 and 14 Oct on Capital.com's US100, 1 min bars, 1 day of history: 26 bars checked.” */
export function testedSaid(tested: RuleTested | null): string {
  if (!tested) return 'Not tested on recorded bars yet: only its source was read.'
  if (tested.failure) return `Tested on ${settingsSaid(tested.settings)}: it failed there.`
  const points = tested.checkpoints
  const checked = points ? `: ${plural(points.run, 'bar')} checked${points.stopped ? ', the time allowed spent' : ''}` : ''
  return `Tested on ${tested.spoken ?? 'the days recorded'}, on ${settingsSaid(tested.settings)}${checked}.`
}

/** A report's badge on a list or a card; none before it is made. */
export function badgeOf(summary: Pick<RuleCheckSummary, 'verdict'> | null | undefined): { label: string; tone: (typeof VERDICT_TONE)[RuleVerdict] } | null {
  return summary ? { label: VERDICT_LABEL[summary.verdict], tone: VERDICT_TONE[summary.verdict] } : null
}

/** Bars as a run test asked for them (“1”, “45S”, “R60”), as the pages name them: “1 min”, “Range 60”. */
export function barsSaid(text: string): string {
  const range = /^R(\d+)$/i.exec(text)
  return barsLabel(range ? { type: 'range', ticks: Number(range[1]) } : { type: 'time', timeframe: text })
}

/** A badge's hint: its worst finding, and whether it was tested on recorded bars. */
export function summarySaid(summary: RuleCheckSummary): string {
  const tested = summary.tested_at ? `Tested on ${summary.tested_bars ? `${barsSaid(summary.tested_bars)} bars` : 'recorded bars'}.` : 'Not tested on recorded bars.'
  return summary.worst && summary.worst.level !== 'note' ? `${summary.worst.message} ${tested}` : tested
}

/** Whether a run test of this is waiting or under way. */
export function testingOf(state: RuleChecksState | undefined, about: RuleCheckReport['about']): 'queued' | 'testing' | null {
  if (!state || !about) return null
  const same = (other: RuleCheckReport['about']) => JSON.stringify(other) === JSON.stringify(about)
  if (state.testing && same(state.testing.about)) return 'testing'
  return state.queued.some((entry) => same(entry.about)) ? 'queued' : null
}

export const TESTING_SAID = { queued: 'Waiting for its run test', testing: 'Being tested on recorded bars' } as const

/** The level a finding counts in, worst first: what the lists sort by. */
export const LEVEL_ORDER: Record<FindingLevel, number> = { blocks: 0, repaints: 1, risk: 2, change: 3, note: 4 }

/** A draft's report, read in the page from the source it holds: what the server makes of a saved version. */
export function draftReport(check: RuleCheck): Pick<RuleCheckReport, 'findings' | 'verdict' | 'tested' | 'alerts' | 'alert_calls' | 'candidates'> {
  return { findings: check.findings, verdict: check.verdict, tested: null, alerts: check.alerts, alert_calls: check.alertCalls, candidates: check.candidates }
}
