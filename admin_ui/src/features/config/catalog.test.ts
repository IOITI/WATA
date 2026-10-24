import { describe, expect, it } from 'vitest'

import { CATALOG, configFile, END, FRESH, MARKET_ZONE, NEWS, NEWS_BEFORE, START, STOPLOSS } from '@/test/configCatalog'

import {
  anchorOf,
  boundsSaid,
  changedParams,
  configPathOf,
  differsFromDefault,
  effectiveValue,
  groupCounts,
  hasRule,
  hrefOf,
  lookup,
  removeAt,
  resetTo,
  ruleOf,
  sameValue,
  searchParams,
  segmentsOf,
  shownValue,
  valueOf,
  withoutRule,
  withRuleOf,
} from './catalog'
import { setAt } from './configModel'

const byPath = (path: string) => {
  const found = lookup(CATALOG, path)
  if (!found) throw new Error(path)
  return found
}

describe('where a parameter is in the file', () => {
  it('finds a rule by its type, wherever it is in the list', () => {
    const config = configFile()

    expect(ruleOf(START)).toBe('market_hours')
    expect(ruleOf(STOPLOSS)).toBeNull()
    expect(configPathOf(config, START)).toEqual(['trade', 'rules', 1, 'rule_config', 'trading_start_hour'])
    expect(valueOf(config, START)).toBe(8)
    expect(valueOf(config, STOPLOSS)).toBe(-7)
  })

  it('has no place for a key of a rule that is absent', () => {
    expect(configPathOf(configFile(), NEWS_BEFORE)).toBeNull()
    expect(valueOf(configFile(), NEWS_BEFORE)).toBeUndefined()
  })

  it('names any rule with the one entry for rule names', () => {
    expect(lookup(CATALOG, 'trade.rules[market_hours].rule_name')?.path).toBe('trade.rules[*].rule_name')
    expect(lookup(CATALOG, 'trade.config.nothing')).toBeUndefined()
  })
})

describe('defaults', () => {
  it('tells a value the services would use anyway from one that changes something', () => {
    expect(differsFromDefault(byPath(FRESH), undefined)).toBe(false)
    expect(differsFromDefault(byPath(FRESH), true)).toBe(false)
    expect(differsFromDefault(byPath(FRESH), false)).toBe(true)
    // A key that is required has no default to differ from.
    expect(differsFromDefault(byPath(START), 9)).toBe(false)
    expect(effectiveValue(byPath(FRESH), undefined)).toBe(true)
    expect(effectiveValue(byPath(START), undefined)).toBeUndefined()
  })

  it('resets to the default, or takes the key out when the default is "not set"', () => {
    const config = setAt(configFile(), ['trade', 'config', 'position_management', 'wait_for_fresh_quote'], false)
    const zoned = setAt(configFile(), ['trade', 'rules', 1, 'rule_config', 'timezone'], 'America/New_York')

    expect(valueOf(resetTo(config as never, byPath(FRESH)), FRESH)).toBe(true)
    const reset = resetTo(zoned as never, byPath(MARKET_ZONE))
    expect(valueOf(reset, MARKET_ZONE)).toBeUndefined()
    expect(valueOf(reset, START)).toBe(8)
  })
})

describe('optional rules', () => {
  it('adds a rule with the settings it is added with, once, and takes it out', () => {
    const added = withRuleOf(configFile(), byPath(NEWS))

    expect(hasRule(added, 'macro_news')).toBe(true)
    expect(valueOf(added, NEWS_BEFORE)).toBe(5)
    expect(withRuleOf(added, byPath(NEWS))).toBe(added)
    expect(hasRule(withoutRule(added, 'macro_news'), 'macro_news')).toBe(false)
  })
})

describe('changes', () => {
  it('names the parameters a draft changed, an added rule included', () => {
    const saved = configFile()
    const draft = withRuleOf(setAt(saved, ['trade', 'rules', 1, 'rule_config', 'trading_start_hour'], 9) as never, byPath(NEWS))

    const changed = changedParams(CATALOG.params, saved, draft)

    expect([...changed].sort()).toEqual([NEWS, NEWS_BEFORE, START].sort())
  })

  it('takes two objects with their keys in another order for the same', () => {
    expect(sameValue({ a: 1, b: [1, 2] }, { b: [1, 2], a: 1 })).toBe(true)
    expect(sameValue({ a: 1 }, { a: 2 })).toBe(false)
    expect(sameValue([1, 2], [2, 1])).toBe(false)
  })

  it('takes a key out without touching the rest', () => {
    const config = configFile()
    const removed = removeAt(config, ['trade', 'config', 'position_management'])

    expect(removed).not.toHaveProperty('trade.config.position_management')
    expect(config).toHaveProperty('trade.config.position_management')
  })
})

describe('finding a parameter', () => {
  it('finds by every word, in the label, the path, the help or the effects', () => {
    expect(searchParams(CATALOG, 'trading start').map((param) => param.path)).toEqual([START])
    expect(searchParams(CATALOG, 'entries hour').map((param) => param.path)).toEqual([START])
    expect(searchParams(CATALOG, '  ')).toEqual([])
  })

  it('links to the row of a parameter on its group page', () => {
    expect(anchorOf(START)).toBe('p-trade-rules-market-hours-rule-config-trading-start-hour')
    expect(hrefOf(byPath(STOPLOSS))).toBe(`/config/exits#${anchorOf(STOPLOSS)}`)
  })

  it('keeps the keys of one rule together', () => {
    const hours = CATALOG.params.filter((param) => param.group === 'hours')

    expect(segmentsOf(hours).map((segment) => [segment.rule, segment.params.length])).toEqual([
      ['market_hours', 3],
      ['macro_news', 2],
    ])
  })
})

describe('what the overview says', () => {
  it('counts what differs, what is unsaved, and what only the server changes or nothing reads', () => {
    const infrastructure = CATALOG.params.filter((param) => param.group === 'infrastructure')
    const exits = CATALOG.params.filter((param) => param.group === 'exits')
    const draft = setAt(configFile(), ['trade', 'config', 'position_management', 'wait_for_fresh_quote'], false)

    expect(groupCounts(infrastructure, configFile(), new Set())).toMatchObject({ total: 2, serverOnly: 1, unused: 1 })
    expect(groupCounts(exits, draft as never, new Set([FRESH]))).toMatchObject({ changedFromDefault: 1, unsaved: 1 })
  })

  it('says values and bounds in words', () => {
    expect(shownValue(undefined)).toBe('not set')
    expect(shownValue(['FOMC', 'CPI'])).toBe('FOMC, CPI')
    expect(shownValue({ us100: '1909050' })).toBe('{"us100":"1909050"}')
    expect(boundsSaid({ minimum: 0, maximum: 23, unit: 'h' })).toBe('From 0 to 23 h')
    expect(boundsSaid({ minimum: 1, maximum: null, unit: '%' })).toBe('At least 1%')
    expect(boundsSaid({ minimum: null, maximum: null })).toBeNull()
    expect(END).toContain('trading_end_hour')
  })
})
