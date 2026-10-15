import { describe, expect, it } from 'vitest'

import type { JsonObject } from '@/lib/types'

import {
  applyEditable,
  diffConfigs,
  getAt,
  parseJsonObject,
  pickEditable,
  restoreChanges,
  ruleConfigPath,
  setAt,
} from './configModel'

const EDITABLE = ['trade.rules', 'trade.config', 'reporting', 'logging.level']

function sampleConfig(): JsonObject {
  return {
    webserver: { app_secret: '********' },
    logging: { level: 'INFO', format: '%(message)s' },
    trade: {
      rules: [
        { rule_type: 'allowed_indices', rule_config: { indice_ids: { us100: '1909050' } } },
        { rule_type: 'risk_management', rule_config: { max_trades_per_day: 8, cooldown_after_loss_minutes: 10 } },
      ],
      config: { turbo_preference: { price_range: { min: 4, max: 15 } } },
      persistant: { last_action_file: '/app/var/lib/trade/last_action.json' },
    },
  }
}

describe('getAt / setAt', () => {
  it('reads through objects and arrays', () => {
    const config = sampleConfig()
    expect(getAt(config, ['trade', 'rules', 1, 'rule_config', 'max_trades_per_day'])).toBe(8)
    expect(getAt(config, ['trade', 'rules', 5, 'rule_config'])).toBeUndefined()
    expect(getAt(config, ['logging', 'level', 'deeper'])).toBeUndefined()
  })

  it('returns a changed copy and leaves the original alone', () => {
    const config = sampleConfig()
    const path = ['trade', 'rules', 1, 'rule_config', 'max_trades_per_day']

    const changed = setAt(config, path, 6)

    expect(getAt(changed, path)).toBe(6)
    expect(getAt(config, path)).toBe(8)
    // Branches off the path are the same objects, not copies.
    expect(getAt(changed, ['trade', 'rules', 0])).toBe(getAt(config, ['trade', 'rules', 0]))
    expect(getAt(changed, ['webserver'])).toBe(getAt(config, ['webserver']))
  })

  it('creates the missing levels of a new path', () => {
    expect(setAt({}, ['a', 'b'], 1)).toEqual({ a: { b: 1 } })
  })
})

describe('ruleConfigPath', () => {
  it('finds a rule by its type, wherever it is in the list', () => {
    expect(ruleConfigPath(sampleConfig(), 'risk_management')).toEqual(['trade', 'rules', 1, 'rule_config'])
  })

  it('is null for a rule that is not configured', () => {
    expect(ruleConfigPath(sampleConfig(), 'day_trading')).toBeNull()
    expect(ruleConfigPath({}, 'day_trading')).toBeNull()
  })
})

describe('editable sections', () => {
  it('picks only what may be edited', () => {
    const picked = pickEditable(sampleConfig(), EDITABLE)

    expect(Object.keys(picked).sort()).toEqual(['logging', 'trade'])
    expect(picked.logging).toEqual({ level: 'INFO' })
    expect(getAt(picked, ['trade', 'persistant'])).toBeUndefined()
    expect(picked.webserver).toBeUndefined()
  })

  it('applies edits to the editable sections only', () => {
    const config = sampleConfig()
    const edited: JsonObject = {
      logging: { level: 'DEBUG', format: 'changed' },
      webserver: { app_secret: 'attacker' },
      trade: { config: { turbo_preference: { price_range: { min: 5, max: 15 } } }, persistant: { last_action_file: '/x' } },
    }

    const result = applyEditable(config, edited, EDITABLE)

    expect(getAt(result, ['logging', 'level'])).toBe('DEBUG')
    expect(getAt(result, ['logging', 'format'])).toBe('%(message)s')
    expect(getAt(result, ['webserver', 'app_secret'])).toBe('********')
    expect(getAt(result, ['trade', 'config', 'turbo_preference', 'price_range', 'min'])).toBe(5)
    expect(getAt(result, ['trade', 'persistant', 'last_action_file'])).toBe('/app/var/lib/trade/last_action.json')
    // A section left out of the edit keeps its value.
    expect(getAt(result, ['trade', 'rules'])).toBe(getAt(config, ['trade', 'rules']))
  })
})

describe('diffConfigs', () => {
  it('is empty for identical configs', () => {
    expect(diffConfigs(sampleConfig(), sampleConfig())).toEqual([])
  })

  it('lists changed, added and removed values by path', () => {
    const before = sampleConfig()
    let after = setAt(before, ['trade', 'rules', 1, 'rule_config', 'max_trades_per_day'], 6)
    after = setAt(after, ['reporting'], { milestones_eur: [1000] })
    after = setAt(after, ['logging'], { level: 'INFO' })

    expect(diffConfigs(before, after as JsonObject)).toEqual([
      { path: 'logging.format', before: '"%(message)s"', after: null },
      { path: 'reporting.milestones_eur[0]', before: null, after: '1000' },
      { path: 'trade.rules[1].rule_config.max_trades_per_day', before: '8', after: '6' },
    ])
  })

  it('notices a list that became empty', () => {
    const before: JsonObject = { dates: ['01/01/2026'] }
    const after: JsonObject = { dates: [] }

    expect(diffConfigs(before, after)).toEqual([
      { path: 'dates', before: null, after: '[]' },
      { path: 'dates[0]', before: '"01/01/2026"', after: null },
    ])
  })
})

describe('restoreChanges', () => {
  const PROTECTED = ['trade.config.signal_polling.service_url', 'trade.config.watchlist_manager.api_port']

  function withAddresses(): JsonObject {
    return setAt(sampleConfig(), ['trade', 'config'], {
      turbo_preference: { price_range: { min: 4, max: 15 } },
      signal_polling: { service_url: 'http://web_server1:80', interval_ms: 500 },
      watchlist_manager: { api_port: 8081, enabled: true },
    }) as JsonObject
  }

  it('lists what a restore puts back: the trading parameters of the backup', () => {
    const current = withAddresses()
    let backup = setAt(current, ['trade', 'config', 'turbo_preference', 'price_range', 'min'], 5)
    backup = setAt(backup, ['logging', 'level'], 'DEBUG')

    expect(restoreChanges(current, backup as JsonObject, EDITABLE, PROTECTED)).toEqual([
      { path: 'logging.level', before: '"INFO"', after: '"DEBUG"' },
      { path: 'trade.config.turbo_preference.price_range.min', before: '4', after: '5' },
    ])
  })

  it('leaves out what a restore does not touch', () => {
    const current = withAddresses()
    // Outside the editable sections: credentials, infrastructure, the admin's own settings.
    let backup = setAt(current, ['webserver', 'app_secret'], 'older')
    backup = setAt(backup, ['logging', 'format'], 'older')
    backup = setAt(backup, ['admin'], { actions_enabled: true })
    backup = setAt(backup, ['trade', 'persistant', 'last_action_file'], '/older')
    // Inside them, but kept as on the server: where the services connect.
    backup = setAt(backup, ['trade', 'config', 'signal_polling', 'service_url'], 'http://older:80')
    backup = setAt(backup, ['trade', 'config', 'watchlist_manager', 'api_port'], 9000)

    expect(restoreChanges(current, backup as JsonObject, EDITABLE, PROTECTED)).toEqual([])
  })

  it('still shows the other settings of a section that holds a protected one', () => {
    const current = withAddresses()
    const backup = setAt(current, ['trade', 'config', 'signal_polling'], { service_url: 'http://older:80', interval_ms: 750 })

    expect(restoreChanges(current, backup as JsonObject, EDITABLE, PROTECTED)).toEqual([
      { path: 'trade.config.signal_polling.interval_ms', before: '500', after: '750' },
    ])
  })
})

describe('parseJsonObject', () => {
  it('accepts an object', () => {
    expect(parseJsonObject('{"logging": {"level": "INFO"}}')).toEqual({ ok: true, value: { logging: { level: 'INFO' } } })
  })

  it('refuses anything else with a reason', () => {
    expect(parseJsonObject('{ not json').ok).toBe(false)
    expect(parseJsonObject('[1, 2]')).toEqual({ ok: false, error: 'The document must be a JSON object' })
    expect(parseJsonObject('null')).toEqual({ ok: false, error: 'The document must be a JSON object' })
  })
})
