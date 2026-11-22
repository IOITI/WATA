// What the pages say of Edge Watch (src/admin/edge_watch.py, edge_watch_service.py): a strategy's trades since its
// reference run set against it. Its verdict in words and tone, the figures said as sentences, the fan chart's
// points, and what holds a rule back from going live (readiness_unmet of edge_watch.py: a change to one is made in
// the other). Pure.

import { formatPercent } from 'ioiti-ui'
import type { BadgeTone, FanPoint } from 'ioiti-ui'

import type { EdgeCone, EdgeOwner, EdgeTrack, EdgeVerdict } from '@/lib/types'

/** The verdicts in words (LABELS of edge_watch.py). */
export const VERDICT_LABEL: Record<EdgeVerdict, string> = {
  too_early: 'Too early to tell',
  no_model: 'Cannot be watched',
  no_edge_over_random: 'No edge over random entries',
  broken: 'Broken',
  watch: 'Worth watching',
  better_than_expected: 'Better than expected',
  as_expected: 'As expected',
}

export const VERDICT_TONE: Record<EdgeVerdict, BadgeTone> = {
  too_early: 'neutral',
  no_model: 'neutral',
  no_edge_over_random: 'warning',
  broken: 'danger',
  watch: 'warning',
  // Luckier than the backtest: no reason to trust it more.
  better_than_expected: 'neutral',
  as_expected: 'success',
}

/** A rule is ready on paper with this many trades since its reference (READY_TRADES of edge_watch.py). */
export const READY_TRADES = 20
/** A result whose share of the reference's paths as bad or worse is under this is unusual (UNUSUAL). */
export const UNUSUAL = 5

/** The track an owner's verdict is of: the live trades, or paper trades. */
export function mainTrack(owner: EdgeOwner): EdgeTrack | undefined {
  return owner.tracks[owner.main] ?? owner.tracks.paper ?? owner.tracks.real
}

/** The track's verdict in a line: "Broken after 40 trades, −12.30 %". */
export function verdictLine(track: EdgeTrack): string {
  const trades = `${track.n} trade${track.n === 1 ? '' : 's'}`
  if (track.verdict === 'no_model' || track.verdict === 'no_edge_over_random') return VERDICT_LABEL[track.verdict]
  return `${VERDICT_LABEL[track.verdict]} after ${trades}, ${formatPercent(track.cumulative, true)}`
}

const share = (value: number) => `${value < 10 ? value.toFixed(1).replace(/\.0$/, '') : Math.round(value)} %`

/** How unusual the result is: the share of the reference's paths that did as badly or worse after as many trades. */
export function rankText(track: EdgeTrack): string | null {
  if (track.rank === null) return track.past_horizon ? 'Past the trades its reference was drawn for.' : null
  const rarer = track.rank_bound === 'at_most' ? 'or fewer ' : track.rank_bound === 'at_least' ? 'or more ' : ''
  return `${share(track.rank)} ${rarer}of the reference’s paths did as badly or worse after ${track.n} trade${track.n === 1 ? '' : 's'}.`
}

export function drawdownText(track: EdgeTrack): string | null {
  if (track.drawdown_rank === null || track.drawdown >= 0) return track.drawdown >= 0 ? 'No fall from a peak yet.' : null
  return `${share(track.drawdown_rank)} of the paths fell as deep (${formatPercent(track.drawdown, true)}) by then.`
}

export function streakText(track: EdgeTrack): string | null {
  if (track.streak_rank === null || track.streak === 0) return track.streak === 0 ? 'No loss yet.' : null
  return `${share(track.streak_rank)} of the paths lost ${track.streak} trade${track.streak === 1 ? '' : 's'} in a row by then.`
}

/** How far the test is from knowing: the trades judged against those it takes to tell a dead edge. */
export function progressText(track: EdgeTrack): string {
  if (track.trades_to_tell === null) {
    return 'Its reference’s edge is too thin for the test to tell it from random entries in a reasonable number of trades.'
  }
  const tested = track.tested ?? track.n
  return `${tested} of the ${track.trades_to_tell} trades it takes to tell a dead edge, 80 % of the time.`
}

/** The test as a share of its threshold, 0 to 1 (1: broken). */
export function testShare(track: EdgeTrack): number | null {
  if (track.cusum === null || track.h === null || track.h <= 0) return null
  return Math.min(1, track.cusum / track.h)
}

/** What a reference's sample can carry, in words: the guard pauses nothing on one that is too few. */
export function sampleText(sample: string | null | undefined): string | null {
  if (sample === 'too_few') return 'Its reference rests on fewer than 30 trades or 20 days: the edge guard pauses nothing on it.'
  if (sample === 'few') return 'Its reference rests on few trades or days.'
  return null
}

// The cone's levels drawn: where 90 % and half of the paths went, and their middle.
const DRAWN = { p5: 5, p25: 25, median: 50, p75: 75, p95: 95 } as const

function levelsAt(cone: EdgeCone, n: number): Record<keyof typeof DRAWN, number> | null {
  const points = cone.n
  if (n < points[0] || n > points[points.length - 1]) return null
  const at = (row: number[]) =>
    Object.fromEntries(Object.entries(DRAWN).map(([key, level]) => [key, row[cone.levels.indexOf(level)]])) as Record<keyof typeof DRAWN, number>
  const place = points.findIndex((point) => point >= n)
  if (points[place] === n) return at(cone.cumulative[place])
  const before = at(cone.cumulative[place - 1])
  const after = at(cone.cumulative[place])
  const weight = (n - points[place - 1]) / (points[place] - points[place - 1])
  return Object.fromEntries(
    Object.keys(DRAWN).map((key) => [key, before[key as keyof typeof DRAWN] + (after[key as keyof typeof DRAWN] - before[key as keyof typeof DRAWN]) * weight]),
  ) as Record<keyof typeof DRAWN, number>
}

/** The fewest trades the chart shows, and how far past the trades judged it goes: the cone just ahead of them. */
export const SHOWN_LEAST = 30
export const SHOWN_AHEAD = 1.5

/**
 * The fan chart's points: the cone at each point of its grid and at each trade of the path, from 0 trades (all at
 * 0), and the path's compounded result after each trade, as far as it goes. The cone is drawn a little past the
 * trades judged (SHOWN_AHEAD of them, SHOWN_LEAST at least), not to its horizon: compounded over hundreds of
 * trades it widens so far that the trades judged would be a flat line at its start.
 */
export function fanPoints(cone: EdgeCone | null | undefined, track: EdgeTrack | undefined): FanPoint[] {
  const path = track?.path ?? []
  const actual = new Map(path.map((step) => [step.n, step.cumulative]))
  const places = new Set<number>([0, ...(cone?.n ?? []), ...path.map((step) => step.n)])
  const ends = cone?.n.length ? cone.n[cone.n.length - 1] : 0
  const shown = Math.max(path.length, Math.min(ends, Math.max(SHOWN_LEAST, Math.ceil(path.length * SHOWN_AHEAD))))
  return [...places]
    .filter((place) => place <= shown)
    .sort((a, b) => a - b)
    .map((place) => {
      const levels = place === 0 ? { p5: 0, p25: 0, median: 0, p75: 0, p95: 0 } : cone ? levelsAt(cone, place) : null
      return {
        x: place,
        p5: levels?.p5 ?? null,
        p25: levels?.p25 ?? null,
        median: levels?.median ?? null,
        p75: levels?.p75 ?? null,
        p95: levels?.p95 ?? null,
        actual: place === 0 ? (path.length ? 0 : null) : actual.get(place) ?? null,
      }
    })
}

/**
 * What a rule's Edge Watch on paper holds back from going live, in the server's words (readiness_unmet of
 * edge_watch.py): empty once its paper trades agree with its reference.
 */
export function edgeUnmet(owner: EdgeOwner | null): string[] {
  const track = owner?.tracks.paper
  if (!owner || !track) return ['no reference run to judge its paper trades against (set one from a run of the rule)']
  if (track.verdict === 'no_model') return [`its reference cannot be watched: ${track.reasons[0] ?? ''}`]
  if (track.verdict === 'no_edge_over_random') return ['its reference does no better than random entries']
  const unmet: string[] = []
  if (track.n < READY_TRADES) unmet.push(`${track.n} paper trade(s) since its reference of ${READY_TRADES}`)
  if (track.verdict === 'broken') unmet.push("its paper trades look like random entries', not like its reference's")
  else if (track.rank !== null && track.rank < UNUSUAL) unmet.push(`its paper result is under ${UNUSUAL} % of its reference's paths`)
  return unmet
}

/** A rule's Edge Watch on paper in the Signal rules list's line: its verdict, or why there is none. */
export function paperVerdict(owner: EdgeOwner | undefined): { text: string; tone: BadgeTone } | null {
  const track = owner?.tracks.paper
  if (!owner || !track) return null
  return { text: verdictLine(track), tone: VERDICT_TONE[track.verdict] }
}

/** Edge Watch's verdict on a live rule's real trades against its reference; null without a real track. */
export function realVerdict(owner: EdgeOwner | undefined): { text: string; tone: BadgeTone } | null {
  const track = owner?.tracks.real
  if (!owner || !track) return null
  return { text: verdictLine(track), tone: VERDICT_TONE[track.verdict] }
}
