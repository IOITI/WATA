import { describe, expect, it } from 'vitest'

import type { Judged } from '@/features/backtests/verdict'
import type { TaRule, TaScriptSummary } from '@/lib/types'
import { listedRun } from '@/test/backtests'

import {
  editorLineHref,
  lineOf,
  modeCounts,
  modesSaid,
  pinnedSaid,
  pinnedVersion,
  rulesRunning,
  runsOfScript,
  runsSaid,
  scriptTabHref,
  scriptTabOf,
  howBy,
  howShown,
  tabsOf,
  versionOf,
  versionUses,
} from './scriptPageModel'

const SCRIPT: Pick<TaScriptSummary, 'id' | 'version' | 'versions'> = {
  id: '0123456789ab',
  version: 3,
  versions: [1, 2, 3].map((number) => ({ number, saved_at: `2026-10-1${number}T10:00:00Z`, size: 40, sha256: String(number) })),
}
const LIBRARY = 'aaaaaaaaaaaa'

function rule(id: string, mode: TaRule['mode'], version: number, changes: Partial<TaRule> = {}): TaRule {
  return {
    id, name: `Rule ${id}`, asset: { provider: 'capital', key: 'US100' }, side: 'bid', bars: { type: 'time', timeframe: '1' },
    script: { id: SCRIPT.id, version, sha256: 'x' }, libraries: [], inputs: [], alerts: [], alert_calls: 'ignore',
    confidence_plot: null, indice: 'us100', mode, created_at: '', updated_at: '', mode_changed_at: '', script_name: null,
    latest: { script: 3, libraries: {} }, ...changes,
  }
}

const RULES = [
  rule('r1', 'live', 3),
  rule('r2', 'shadow', 2),
  rule('r3', 'shadow', 3),
  // Another script's rule, which imports the library at its second version.
  rule('r4', 'off', 1, { script: { id: 'bbbbbbbbbbbb', version: 1, sha256: 'y' }, libraries: [{ id: LIBRARY, version: 2, sha256: 'z', name: 'Lib' }] }),
]

describe('the tabs of a script', () => {
  it('has a tab for each view, a library none of backtests, nor any when backtests are off', () => {
    expect(tabsOf('indicator', true)).toEqual(['editor', 'chart', 'how', 'rule', 'backtests', 'rules', 'history'])
    // A library is no rule's script (its rule check is the scripts' that import it), and draws nothing alone.
    expect(tabsOf('library', true)).toEqual(['editor', 'how', 'rules', 'history'])
    expect(tabsOf('strategy', false)).toEqual(['editor', 'chart', 'how', 'rule', 'rules', 'history'])
  })

  it('opens the tab its address names, the editor otherwise', () => {
    const tabs = tabsOf('library', true)
    expect(scriptTabOf('rules', tabs)).toBe('rules')
    expect(scriptTabOf('backtests', tabs)).toBe('editor')
    expect(scriptTabOf(null, tabs)).toBe('editor')
    expect(scriptTabHref('editor')).toBe('?')
    expect(scriptTabHref('history')).toBe('?tab=history')
    expect(scriptTabHref('backtests', 2)).toBe('?tab=backtests&version=2')
    expect(scriptTabHref('rule', 3)).toBe('?tab=rule&version=3')
    expect(editorLineHref(12)).toBe('?line=12')
    expect([lineOf('12'), lineOf('0'), lineOf('x'), lineOf(null)]).toEqual([12, null, null, null])
    expect(versionOf('2', SCRIPT)).toBe(2)
    expect(versionOf('7', SCRIPT)).toBeNull()
    expect(versionOf(null, SCRIPT)).toBeNull()
  })
})

describe('what runs a script', () => {
  it('finds the rules running it at any version, a library through the rules that pin it', () => {
    expect(rulesRunning(SCRIPT.id, RULES).map((entry) => entry.id)).toEqual(['r1', 'r2', 'r3'])
    expect(rulesRunning(LIBRARY, RULES).map((entry) => entry.id)).toEqual(['r4'])
    expect(pinnedVersion(RULES[3], LIBRARY)).toBe(2)
    expect(pinnedVersion(RULES[3], SCRIPT.id)).toBeNull()
  })

  it('says the modes they are in, live first, and the version each pins against the newest', () => {
    expect(modesSaid(modeCounts(rulesRunning(SCRIPT.id, RULES)))).toBe('1 live · 2 in shadow')
    expect(modesSaid(modeCounts([]))).toBe('None')
    expect(pinnedSaid(RULES[0], SCRIPT)).toBe('Runs v3, the newest')
    expect(pinnedSaid(RULES[1], SCRIPT)).toBe('Runs v2; v3 is the newest')
    expect(pinnedSaid(RULES[3], { id: LIBRARY, version: 3 })).toBe('Runs v2; v3 is the newest')
  })

  it('counts its own runs and those that pass every check, not its stresses', () => {
    const of = (id: string, version: number, changes = {}) =>
      listedRun({ id, source: { kind: 'pine', script: { id: SCRIPT.id, version, name: 'EMA' }, from_rule: null, signals: null }, ...changes })
    const runs = [of('a', 3), of('b', 2), of('s', 3, { stress_of: 'a', kind: 'sweep' }), listedRun({ id: 'other' })]
    const judged = new Map<string, Judged>([
      ['a', { checks: [], verdict: { tone: 'pass', sentence: '' } }],
      ['b', { checks: [], verdict: { tone: 'fail', sentence: '' } }],
    ])

    expect(runsOfScript(runs, SCRIPT.id).map((run) => run.id)).toEqual(['a', 'b', 's'])
    expect(runsSaid(runsOfScript(runs, SCRIPT.id), judged)).toEqual({ runs: 2, passing: 1 })
    // Each version with its runs and the rules that pin it.
    const uses = versionUses(SCRIPT, runs, RULES)
    expect(uses.get(3)).toEqual({ runs: 2, rules: [RULES[0], RULES[2]] })
    expect(uses.get(2)).toEqual({ runs: 1, rules: [RULES[1]] })
    expect(uses.get(1)).toEqual({ runs: 0, rules: [] })
  })
})

describe('a script’s How it works', () => {
  it('says who wrote it, and leads to LuxAlgo’s page or the agent’s conversation', () => {
    const luxalgo = { provider: 'luxalgo' as const, slug: 'vwap', url: 'https://www.luxalgo.com/library/indicator/vwap/' }

    expect(howBy({ by: 'operator', from: null })).toEqual({ said: 'you', href: null })
    expect(howBy({ by: 'agent:c0ffee12', from: null })).toEqual({ said: 'the AI agent', href: '/agent/c0ffee12' })
    expect(howBy({ by: 'operator', from: luxalgo })).toEqual({ said: 'LuxAlgo’s write-up', href: luxalgo.url })
    expect(howBy({ by: 'operator', from: { script: '0123456789ab' } })).toEqual({
      said: 'copied with the script', href: '/ta/indicators/0123456789ab?tab=how',
    })
    // Nothing else becomes an address.
    expect(howBy({ by: 'agent:../../x', from: null })).toEqual({ said: 'agent:../../x', href: null })
  })

  it('is drawn without images, LuxAlgo’s as its Library’s pages draw it', () => {
    expect(howShown('# Mine\n\n![chart](https://example.com/a.png)\nIt crosses.', null)).toBe('# Mine\n\n\nIt crosses.')
    expect(howShown('# VWAP\n\nSee [the bands](/library/indicator/vwap-bands/).', { provider: 'luxalgo' })).toBe(
      'See [the bands](/ta/luxalgo/indicators/vwap-bands).',
    )
  })
})
