import { describe, expect, it } from 'vitest'

import type { EdgeCone, EdgeOwner, EdgeTrack } from '@/lib/types'

import {
  drawdownText,
  edgeUnmet,
  fanPoints,
  mainTrack,
  paperVerdict,
  progressText,
  rankText,
  streakText,
  testShare,
  verdictLine,
} from './edgeWatch'

const LEVELS = [1, 2.5, 5, 10, 25, 50, 75, 90, 95, 97.5, 99]
// A cone at 1, 2 and 4 trades: each level k at n trades is (k - 50) / 10 * n.
const CONE: EdgeCone = {
  levels: LEVELS,
  n: [1, 2, 4],
  cumulative: [1, 2, 4].map((n) => LEVELS.map((level) => ((level - 50) / 10) * n)),
  drawdown: [1, 2, 4].map(() => LEVELS.map(() => 0)),
  streak_at_least: [[50], [50, 20], [60, 30, 10]],
}

function track(changes: Partial<EdgeTrack> = {}): EdgeTrack {
  return {
    verdict: 'as_expected', reasons: ['its trades are what its reference led to expect'], n: 15, cumulative: -7.85, rank: 18,
    drawdown: -12.1, drawdown_rank: 29, streak: 3, streak_rank: 40, cusum: 1.5, h: 6, alarm_at: null, trades_to_tell: 57,
    progress: 0.26, settings_differ: [], ...changes,
  }
}

function owner(paper: Partial<EdgeTrack> = {}): EdgeOwner {
  return {
    owner: '330ba671bfc0', name: 'Kernel VWAP', mode: 'shadow', reference: null, since: '2026-10-31', model: null,
    verdict: paper.verdict ?? 'as_expected', main: 'paper', tracks: { paper: track(paper) },
  }
}

describe('Edge Watch in words', () => {
  it('says a verdict with its trades and result', () => {
    expect(verdictLine(track())).toBe('As expected after 15 trades, −7.85%')
    expect(verdictLine(track({ verdict: 'broken', n: 1, cumulative: -3 }))).toBe('Broken after 1 trade, −3.00%')
    expect(verdictLine(track({ verdict: 'no_edge_over_random' }))).toBe('No edge over random entries')
  })

  it('says how unusual a result, a fall and a streak are, against the reference’s paths', () => {
    expect(rankText(track())).toBe('18 % of the reference’s paths did as badly or worse after 15 trades.')
    expect(rankText(track({ rank: 1, rank_bound: 'at_most' }))).toBe('1 % or fewer of the reference’s paths did as badly or worse after 15 trades.')
    expect(rankText(track({ rank: null, past_horizon: true }))).toBe('Past the trades its reference was drawn for.')
    expect(drawdownText(track())).toBe('29 % of the paths fell as deep (−12.10%) by then.')
    expect(drawdownText(track({ drawdown: 0, drawdown_rank: 100 }))).toBe('No fall from a peak yet.')
    expect(streakText(track())).toBe('40 % of the paths lost 3 trades in a row by then.')
  })

  it('says how far the test is from telling a dead edge', () => {
    expect(progressText(track())).toBe('15 of the 57 trades it takes to tell a dead edge, 80 % of the time.')
    expect(progressText(track({ trades_to_tell: null }))).toContain('too thin')
    expect(testShare(track())).toBe(0.25)
    expect(testShare(track({ cusum: 9 }))).toBe(1)
    expect(testShare(track({ cusum: null }))).toBeNull()
  })

  it('reads an owner’s main track', () => {
    const live: EdgeOwner = { ...owner(), owner: 'live', main: 'real', tracks: { real: track({ n: 3 }), paper: track() } }
    expect(mainTrack(live)?.n).toBe(3)
    expect(mainTrack(owner())?.n).toBe(15)
  })
})

describe('the fan chart’s points', () => {
  it('starts at 0, keeps the cone at its grid and at each trade of the path, the path as far as it goes', () => {
    const path = [1, 2, 3].map((n) => ({ n, at: `2026-11-0${n}T10:00:00Z`, result: 1, cumulative: n * 1.01 }))
    const points = fanPoints(CONE, track({ path }))

    expect(points.map((point) => point.x)).toEqual([0, 1, 2, 3, 4])
    expect(points[0]).toEqual({ x: 0, p5: 0, p25: 0, median: 0, p75: 0, p95: 0, actual: 0 })
    // Between 2 and 4 trades, the cone is drawn halfway: at 3 trades its 95th level is (95 - 50) / 10 * 3.
    expect(points[3].p95).toBeCloseTo(13.5)
    expect(points[3].actual).toBeCloseTo(3.03)
    expect(points[4].actual).toBeNull()
  })

  it('draws the cone a little past the trades judged, not to its horizon', () => {
    const long: EdgeCone = { ...CONE, n: Array.from({ length: 200 }, (_, index) => index + 1), cumulative: Array.from({ length: 200 }, () => LEVELS.map(() => 0)) }
    const path = Array.from({ length: 40 }, (_, index) => ({ n: index + 1, at: '2026-11-03T10:00:00Z', result: 0, cumulative: 0 }))
    expect(fanPoints(long, track({ path })).at(-1)?.x).toBe(60)
    expect(fanPoints(long, track({ path: path.slice(0, 5) })).at(-1)?.x).toBe(30)
  })

  it('draws the cone alone when nothing was traded yet', () => {
    const points = fanPoints(CONE, track({ path: [] }))
    expect(points[0].actual).toBeNull()
    expect(points.every((point) => point.actual === null)).toBe(true)
  })
})

describe('what holds a rule back from going live (readiness_unmet of edge_watch.py)', () => {
  it('says the server’s words', () => {
    expect(edgeUnmet(null)).toEqual(['no reference run to judge its paper trades against (set one from a run of the rule)'])
    expect(edgeUnmet(owner({ n: 24 }))).toEqual([])
    expect(edgeUnmet(owner({ n: 12 }))).toEqual(['12 paper trade(s) since its reference of 20'])
    expect(edgeUnmet(owner({ verdict: 'broken', n: 31 }))).toEqual(["its paper trades look like random entries', not like its reference's"])
    expect(edgeUnmet(owner({ verdict: 'watch', n: 24, rank: 3 }))).toEqual(["its paper result is under 5 % of its reference's paths"])
    expect(edgeUnmet(owner({ verdict: 'no_model', reasons: ['its reference has 9 trade(s)'] }))).toEqual([
      'its reference cannot be watched: its reference has 9 trade(s)',
    ])
  })

  it('gives the rules list its line and tone', () => {
    expect(paperVerdict(owner({ verdict: 'broken', n: 31, cumulative: -18.4 }))).toEqual({ text: 'Broken after 31 trades, −18.40%', tone: 'danger' })
    expect(paperVerdict(undefined)).toBeNull()
  })
})
