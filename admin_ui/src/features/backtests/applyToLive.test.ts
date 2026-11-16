import { describe, expect, it } from 'vitest'

import type { RunSettingDifference } from '@/lib/types'

import { differencesSaid, differenceTag, differenceValue, initiallyTicked, reviewBlocked } from './applyToLive'

function row(changes: Partial<RunSettingDifference> = {}): RunSettingDifference {
  return {
    setting: 'stoploss_percent', label: 'Stop-loss', unit: '%', group: 'exits', before: -20, after: -10, own: true,
    paths: ['trade.config.position_management.performance_thresholds.stoploss_percent'], apply: true, why_not: null,
    note: null, ...changes,
  }
}

describe("a run's settings applied to WATA", () => {
  it('says each value as the run page says a setting, the releases listed', () => {
    expect(differenceValue(-10, row())).toBe('-10 %')
    expect(differenceValue(true, row({ unit: '' }))).toBe('On')
    expect(differenceValue(['FOMC', 'CPI'], row({ unit: '' }))).toBe('FOMC, CPI')
    expect(differenceValue([], row({ unit: '' }))).toBe('None')
    // A time of day is said without its unit, as the form says it.
    expect(differenceValue('15:55', row({ unit: 'local time' }), { kind: 'time', unit: 'local time' })).toBe('15:55')
  })

  it('tells a change the run made from one WATA made since the run was asked', () => {
    expect(differenceTag(row())).toBe('Changed by this run')
    expect(differenceTag(row({ own: false }))).toBe('WATA’s then, changed since')
  })

  it('ticks every setting that can be applied, so that WATA trades as the run did', () => {
    const rows = [row(), row({ setting: 'cooldown_after_loss_minutes', own: false }), row({ setting: 'trading_end_hour', apply: false, why_not: 'x' })]

    expect(initiallyTicked(rows)).toEqual(['stoploss_percent', 'cooldown_after_loss_minutes'])
  })

  it('holds the review back with editing off or nothing ticked', () => {
    expect(reviewBlocked(['stoploss_percent'], true)).toBeNull()
    expect(reviewBlocked([], true)).toBe('Tick at least one setting to apply.')
    expect(reviewBlocked(['stoploss_percent'], false)).toMatch(/admin\.config_edit_enabled/)
  })

  it('says how many settings differ, and whose they are', () => {
    expect(differencesSaid([])).toBe('WATA already trades with this run’s settings.')
    expect(differencesSaid([row(), row({ setting: 'x', own: false })]))
      .toBe('2 settings differ from WATA’s now: 1 the run changed, 1 WATA has changed since the run was asked.')
    expect(differencesSaid([row()])).toBe('1 setting differs from WATA’s now: 1 the run changed.')
  })
})
