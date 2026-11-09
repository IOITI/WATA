import { formatNumber, formatPercent } from 'ioiti-ui'
import type { Check, CheckState } from 'ioiti-ui'

import type {
  BacktestModelCheck,
  BacktestRestsOn,
  BacktestRun,
  BacktestRunListed,
  BacktestSample,
  BacktestSummary,
  BacktestSweepGrid,
  BacktestTurbos,
} from '@/lib/types'

import { EDGE_BEATEN, EDGE_T, headlineEdge, horizonLabel, LEAST_SIGNALS } from './edge'
import { isActive, sampleWarning } from './model'
import { restsOnCheck } from './restsOn'
import { MAX_OVERFITTING, MIN_DEFLATED_SHARPE, stressVerdict } from './robustness'
import { sweepVerdict } from './sweep'

/**
 * Whether a run can be believed, check by check: the bar Claude's research is
 * held to ("Do not believe" in src/admin/mcp_server/instructions.py), said on
 * the run's own page, so that the pages and Claude judge a run alike. Pure.
 */

/** Fewer trades or days than these describe those days and nothing else. */
export const MIN_TRADES = 30
export const MIN_DAYS = 20
/** The share of runs at random entries (%) a run must do better than. */
export const MIN_BEATEN = 95
// A sweep's limits, MAX_OVERFITTING and MIN_DEFLATED_SHARPE, are robustness.ts's, which says the same of them.

/** The part of a run's page that says more of a check: an anchor of it. */
export type RunSection = 'trust' | 'edge' | 'stress' | 'model-check' | 'choice' | 'setup'

export interface RunCheck extends Check {
  id: 'sample' | 'data' | 'random' | 'edge' | 'stress' | 'model' | 'held-out' | 'overfitting' | 'deflated' | 'code'
  section: RunSection
}

/** The runs made from this one, or for it, that a check reads. */
export interface Derived {
  /** The newest stress sweep of the run; its grid once it is done. */
  stress: { run: BacktestRunListed; sweep: BacktestSweepGrid | null } | null
  /** For a run on made-up turbos: the newest model check done of a run on recorded ones, with what it found. */
  modelCheck: { run: BacktestRunListed; found: Pick<BacktestModelCheck, 'paired' | 'correlation'> | null } | null
  /** A sweep's own grid. */
  sweep: BacktestSweepGrid | null
}

/**
 * What the checks read of a finished run: its whole summary on its page (``runChecks``), its headline in the
 * list (``listedChecks``), which the server fills with the same figures, so that the two say the same.
 */
interface Evidence {
  kind: 'run' | 'sweep'
  turbos: BacktestTurbos | undefined
  stale: boolean
  /** Made again on today's code since: its new run says what holds. */
  remade: boolean
  /** A run's figures; none for a run that has not any, which is not judged. */
  figures: { trades: number; days: number; sample?: BacktestSample } | null
  /** The days a sweep was tested on. */
  daysTested: number
  random: { beaten: number; repetitions: number | null } | null
  edge: NonNullable<BacktestSummary['edge']> | null
  pbo: { probability: number } | null
  deflated: { probability: number; trials: number } | null
  /** What a run's result rests on beyond the recorded quotes; none for a run of before. */
  restsOn: BacktestRestsOn | null
}

function evidenceOfRun(run: BacktestRun): Evidence | null {
  const summary = run.summary
  if (!summary) return null
  const figures = summary.figures
  return {
    kind: run.kind,
    turbos: run.turbos,
    stale: Boolean(run.stale),
    remade: Boolean(run.remade_by),
    figures: figures ? { trades: figures.trades, days: figures.days, sample: figures.sample } : null,
    daysTested: summary.days.tested,
    random: summary.against_random ? { beaten: summary.against_random.beaten, repetitions: summary.against_random.repetitions } : null,
    edge: summary.edge ?? null,
    pbo: summary.pbo ?? null,
    deflated: summary.deflated_sharpe ?? null,
    restsOn: summary.rests_on ?? null,
  }
}

function evidenceOfListed(run: BacktestRunListed): Evidence | null {
  const headline = run.headline
  if (!headline) return null
  return {
    kind: run.kind,
    turbos: run.turbos,
    stale: Boolean(run.stale),
    remade: Boolean(run.remade_by),
    figures:
      headline.days !== null && headline.days !== undefined
        ? { trades: headline.trades ?? 0, days: headline.days, sample: headline.sample ?? undefined }
        : null,
    daysTested: headline.days_tested ?? 0,
    random: headline.beaten !== null ? { beaten: headline.beaten, repetitions: headline.repetitions ?? null } : null,
    edge: headline.edge ?? null,
    pbo: headline.pbo ?? null,
    deflated: headline.deflated_sharpe ?? null,
    restsOn: headline.rests_on ?? null,
  }
}

export const NO_DERIVED: Derived = { stress: null, modelCheck: null, sweep: null }

const plural = (count: number, word: string) => `${formatNumber(count)} ${word}${count === 1 ? '' : 's'}`

/** The newest stress sweep of a run (the list is newest first). */
export function newestStress(runs: readonly BacktestRunListed[], runId: string): BacktestRunListed | null {
  return runs.find((entry) => entry.stress_of === runId) ?? null
}

/** The newest model check that is done, whichever run it checks: it says how far the made-up turbos are from the recorded ones. */
export function newestModelCheck(runs: readonly BacktestRunListed[]): BacktestRunListed | null {
  return runs.find((entry) => entry.model_check_of && entry.status.state === 'done') ?? null
}

/** A run's trades and days (a sweep's days alone), said as the page always said them (``sampleWarning``). */
function sampleCheck(trades: number | null, days: number, sample?: BacktestSample): RunCheck {
  const enough = (trades === null || trades >= MIN_TRADES) && days >= MIN_DAYS
  const said = trades === null ? null : sampleWarning(sample ?? (enough ? 'fair' : 'too_few'), trades, days)
  const counted = trades === null ? plural(days, 'day') : `${plural(trades, 'trade')} over ${plural(days, 'day')}`
  return {
    id: 'sample',
    label: 'Enough trades and days',
    state: enough ? 'pass' : 'fail',
    detail: said ?? (enough ? `${counted}.` : `${counted}: under ${MIN_DAYS} days, the figures describe those days and nothing else.`),
    section: 'trust',
  }
}

function codeCheck(evidence: Evidence): RunCheck {
  return {
    id: 'code',
    label: 'Made with today’s code',
    // Not a fault of the strategy: what it found may come out otherwise now.
    state: evidence.stale ? 'open' : 'pass',
    detail: !evidence.stale
      ? 'Made with the code the admin runs now.'
      : evidence.remade
        ? 'Made with other code than the admin runs now, and made again on it since: the run that did says what holds.'
        : 'Made with other code than the admin runs now: make it again to see whether it still holds.',
    section: 'setup',
  }
}

/** The checks of a run that traded on its own (not a sweep, a stress or a model check). */
function singleChecks(evidence: Evidence, derived: Derived): RunCheck[] {
  const figures = evidence.figures
  if (!figures) return []
  const checks: RunCheck[] = [sampleCheck(figures.trades, figures.days, figures.sample)]

  checks.push({ id: 'data', label: 'Priced at recorded quotes', ...restsOnCheck(evidence.restsOn), section: 'trust' })

  const random = evidence.random
  checks.push({
    id: 'random',
    label: 'Beats random entries',
    state: !random ? 'open' : random.beaten >= MIN_BEATEN ? 'pass' : 'fail',
    detail: !random
      ? 'No random entries were set against it: nothing tells its entries from the exit rules.'
      : `Better than ${formatPercent(random.beaten, false, 0)} of ${random.repetitions ?? 'its'} runs at random entries` +
        (random.beaten >= MIN_BEATEN ? '.' : `: ${MIN_BEATEN} % is the bar.`),
    section: 'trust',
  })

  const edge = evidence.edge ? headlineEdge(evidence.edge) : null
  checks.push({
    id: 'edge',
    label: 'An edge in its entries',
    state: !edge || edge.tone === 'unknown' ? 'open' : edge.tone === 'edge' ? 'pass' : 'fail',
    detail: !edge
      ? 'Not measured: the run was made before entries were measured.'
      : edge.tone === 'unknown'
        ? `Too few entry signals with a price to tell (fewer than ${LEAST_SIGNALS} at every horizon).`
        : `At its strongest, after ${horizonLabel(edge.minutes ?? 0)}: t ${formatNumber(edge.t ?? 0, 1)}, ${formatPercent(edge.beaten ?? 0, false, 0)} of random moments beaten` +
          (edge.tone === 'edge' ? '.' : ` (t ${EDGE_T} and ${EDGE_BEATEN} % are the bar).`),
    section: 'edge',
  })

  const stress = derived.stress
  const held = stress?.sweep ? stressVerdict(stress.sweep)?.holds ?? null : null
  checks.push({
    id: 'stress',
    label: 'Survives later and dearer fills',
    state: held === true ? 'pass' : held === false ? 'fail' : 'open',
    detail: !stress
      ? 'Not stressed yet.'
      : isActive(stress.run.status.state)
        ? 'Being stressed.'
        : stress.run.status.state !== 'done'
          ? 'The stress did not finish: ask for it again.'
          : held === true
            ? 'Still above zero with each order a step later and dearer.'
            : held === false
              ? 'Below zero with each order a step later and dearer: it lives on its own fills.'
              : 'The stress gave no figure a step later and dearer.',
    section: 'stress',
  })

  if (evidence.turbos === 'modelled') {
    const check = derived.modelCheck
    const found = check?.found
    checks.push({
      id: 'model',
      label: 'Made-up turbos checked',
      state: check ? 'pass' : 'open',
      detail: !check
        ? 'No run on recorded turbos has been made again on made-up ones: how far these are from real turbos is not known.'
        : found
          ? `Checked on ${plural(found.paired, 'trade')} of the recorded turbos` +
            (found.correlation === null ? '.' : `: a correlation of ${formatNumber(found.correlation, 2)} between their results.`)
          : 'Checked on a run of the recorded turbos.',
      section: 'model-check',
    })
  }
  checks.push(codeCheck(evidence))
  return checks
}

/** The checks of a sweep: whether choosing among its combinations can be believed. */
function sweepChecks(evidence: Evidence, derived: Derived): RunCheck[] {
  const checks: RunCheck[] = [sampleCheck(null, evidence.daysTested)]

  const held = derived.sweep ? sweepVerdict(derived.sweep)?.held ?? null : null
  checks.push({
    id: 'held-out',
    label: 'Holds on days held out',
    state: held === true ? 'pass' : held === false ? 'fail' : 'open',
    detail:
      held === true
        ? 'The best combination stayed in the better half on days that had no say in the choice.'
        : held === false
          ? 'The best combination fell to the worse half on days that had no say in the choice.'
          : 'No days were held out to tell.',
    section: 'choice',
  })

  const pbo = evidence.pbo
  checks.push({
    id: 'overfitting',
    label: 'Little chance of overfitting',
    state: !pbo ? 'open' : pbo.probability <= MAX_OVERFITTING ? 'pass' : 'fail',
    detail: !pbo
      ? 'Not measured: it takes 8 days.'
      : `A probability of ${formatNumber(pbo.probability, 2)} that the best was fitted` +
        (pbo.probability <= MAX_OVERFITTING ? '.' : ` (${MAX_OVERFITTING} at most).`),
    section: 'choice',
  })

  const deflated = evidence.deflated
  checks.push({
    id: 'deflated',
    label: 'Its Sharpe ratio, deflated',
    state: !deflated ? 'open' : deflated.probability >= MIN_DEFLATED_SHARPE ? 'pass' : 'fail',
    detail: !deflated
      ? 'Not measured: it takes 20 days to choose on.'
      : `${formatPercent(deflated.probability * 100, false, 0)} likely to be real after ${plural(deflated.trials, 'trial')}` +
        (deflated.probability >= MIN_DEFLATED_SHARPE ? '.' : ` (${formatPercent(MIN_DEFLATED_SHARPE * 100, false, 0)} is the bar).`),
    section: 'choice',
  })
  checks.push(codeCheck(evidence))
  return checks
}

function checksOf(evidence: Evidence | null, derived: Derived): RunCheck[] {
  if (!evidence) return []
  return evidence.kind === 'sweep' ? sweepChecks(evidence, derived) : singleChecks(evidence, derived)
}

/** Whether a run is judged at all: a stress sweep (of either kind) or a model check judges another run; a run not done, nothing yet. */
export function isJudged(run: Pick<BacktestRunListed, 'status' | 'stress_of' | 'noise_of' | 'model_check_of'>): boolean {
  return run.status.state === 'done' && !run.stress_of && !run.noise_of && !run.model_check_of
}

/**
 * Every check a done run is held to; none for what is not judged so: a stress
 * sweep or a model check (they judge another run), a run not done.
 */
export function runChecks(run: BacktestRun, derived: Derived = NO_DERIVED): RunCheck[] {
  return isJudged(run) ? checksOf(evidenceOfRun(run), derived) : []
}

/** The runs a listed run's checks read, from the list itself: its newest stress, the newest model check, its own grid. */
export function listedDerived(run: BacktestRunListed, runs: readonly BacktestRunListed[]): Derived {
  const stress = newestStress(runs, run.id)
  const check = run.turbos === 'modelled' ? newestModelCheck(runs) : null
  return {
    stress: stress ? { run: stress, sweep: stress.headline?.grid ?? null } : null,
    modelCheck: check ? { run: check, found: check.headline?.model_check ?? null } : null,
    sweep: run.headline?.grid ?? null,
  }
}

/** A listed run's checks, from its headline and the runs listed with it: those of its page (``runChecks``). */
export function listedChecks(run: BacktestRunListed, runs: readonly BacktestRunListed[]): RunCheck[] {
  return isJudged(run) ? checksOf(evidenceOfListed(run), listedDerived(run, runs)) : []
}

export interface RunVerdict {
  tone: CheckState
  sentence: string
}

/** A run's checks and what they say together, as its page says them. */
export interface Judged {
  checks: RunCheck[]
  verdict: RunVerdict
}

/** Each listed run's checks, read from the list itself (``listedChecks``); a run not judged has none. */
export function judgedRuns(runs: readonly BacktestRunListed[]): Map<string, Judged> {
  const found = new Map<string, Judged>()
  for (const run of runs) {
    const checks = listedChecks(run, runs)
    if (checks.length > 0) found.set(run.id, { checks, verdict: verdictOf(checks) })
  }
  return found
}

/** What the checks say together: a fail outweighs everything, a check left open is said. */
export function verdictOf(checks: readonly RunCheck[]): RunVerdict {
  const named = (list: readonly RunCheck[]) => list.map((check) => check.label.toLowerCase()).join(', ')
  const failed = checks.filter((check) => check.state === 'fail')
  const open = checks.filter((check) => check.state === 'open')
  if (failed.length > 0) return { tone: 'fail', sentence: `Not to be believed yet: ${named(failed)}.` }
  if (open.length > 0) {
    return { tone: 'open', sentence: `Nothing against it so far; ${open.length === 1 ? 'one check is' : `${open.length} checks are`} still open: ${named(open)}.` }
  }
  return { tone: 'pass', sentence: 'It passes every check the research is held to.' }
}
