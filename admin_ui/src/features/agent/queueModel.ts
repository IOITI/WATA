// The research queue as its page shows it (src/admin/research_queue.py): who filed each hypothesis, where it stands,
// what the form checks before it is sent, and a hypothesis made of what a run's autopsy found. Pure.

import type { BadgeTone } from 'ioiti-ui'

import type { BacktestAutopsy, ExperimentCriterion, QueueDraft, QueueItem, QueueState } from '@/lib/types'

export const QUEUE_STATE: Record<QueueState, { label: string; tone: BadgeTone }> = {
  waiting: { label: 'Waiting', tone: 'neutral' },
  taken: { label: 'Being tried', tone: 'warning' },
  done: { label: 'Tried', tone: 'success' },
  dropped: { label: 'Dropped', tone: 'neutral' },
}

const MAX_TITLE = 120
const DAY = /^\d{4}-\d{2}-\d{2}$/

/** The run a hypothesis filed from an autopsy came from, or null. */
export function sourceRun(item: Pick<QueueItem, 'source'>): string | null {
  return item.source?.startsWith('autopsy:') ? item.source.slice('autopsy:'.length) : null
}

/** The research task that filed a hypothesis, or null for the operator's. */
export function filedByTask(item: Pick<QueueItem, 'by'>): string | null {
  return item.by.startsWith('agent:') ? item.by.slice('agent:'.length) : null
}

/** Who filed it, in words. */
export function filedBy(item: Pick<QueueItem, 'by' | 'source'>): string {
  if (filedByTask(item)) return 'A research task'
  return sourceRun(item) ? 'You, from a run’s autopsy' : 'You'
}

/** What a run must show, in words. */
export function criteriaSaid(criteria: ExperimentCriterion[]): string {
  return criteria
    .map((criterion) => `${criterion.figure.replaceAll('_', ' ')} ${criterion.bound === 'at_least' ? 'at least' : 'at most'} ${criterion.value}`)
    .join('; ')
}

/** The hypotheses waiting, in the order the autopilot takes them (the server's), and the others. */
export function splitQueue(items: QueueItem[]): { waiting: QueueItem[]; others: QueueItem[] } {
  return { waiting: items.filter((item) => item.state === 'waiting'), others: items.filter((item) => item.state !== 'waiting') }
}

/**
 * The place an operator's hypothesis moves to among theirs, one up or one down; null at either end. The server places
 * it among the operator's others (without it), so one up is its place less one, one down its place and one more.
 */
export function movedTo(waiting: QueueItem[], id: string, step: -1 | 1): number | null {
  const theirs = waiting.filter((item) => item.by === 'operator')
  const place = theirs.findIndex((item) => item.id === id)
  if (place < 0) return null
  const next = place + step
  if (next < 0 || next >= theirs.length) return null
  return next
}

/** A research task's hypothesis taken as the operator's: last of theirs, so tried before every task's. */
export function asYours(waiting: QueueItem[]): number {
  return waiting.filter((item) => item.by === 'operator').length
}

/** The days a hypothesis came from, as typed: separated by commas or spaces. */
export function parseDays(text: string): string[] {
  return text.split(/[\s,]+/).map((part) => part.trim()).filter(Boolean)
}

export function emptyDraft(): QueueDraft {
  return { title: '', hypothesis: '', family: '', rationale: null, motivated_by: [] }
}

/** Why the form cannot be sent yet, or null. */
export function draftProblem(draft: QueueDraft): string | null {
  if (!draft.title.trim()) return 'Give it a title'
  if (draft.title.trim().length > MAX_TITLE) return `A title is ${MAX_TITLE} characters at most`
  if (!draft.hypothesis.trim()) return 'Say what the data could refute'
  if (!draft.family.trim()) return 'Name its family of ideas'
  const wrong = draft.motivated_by.find((day) => !DAY.test(day))
  if (wrong) return `${wrong} is not a day (YYYY-MM-DD)`
  return null
}

/** A hypothesis of what a run's autopsy found held on its later days: the operator files it from the run's page. */
export function hypothesisFromAutopsy(
  held: BacktestAutopsy['held'][number],
  autopsy: Pick<BacktestAutopsy, 'choose_days' | 'test_days'>,
  run: { id: string; name: string | null; family: string | null },
): QueueDraft {
  const title = `${held.said}`.slice(0, MAX_TITLE)
  const chosen = autopsy.choose_days ? `chosen on ${autopsy.choose_days[0]} to ${autopsy.choose_days[1]}` : 'chosen on its first days'
  const tested = autopsy.test_days ? `held on ${autopsy.test_days[0]} to ${autopsy.test_days[1]}` : 'held on its later days'
  return {
    title,
    hypothesis: held.sentence,
    family: run.family ?? 'autopsy',
    rationale: `The autopsy of ${run.name ?? 'run'} ${run.id}: ${chosen}, ${tested} (q ${held.q}). A hypothesis to test on other days, never a result.`,
    motivated_by: [],
    source: `autopsy:${run.id}`,
  }
}
