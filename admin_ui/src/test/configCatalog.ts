/** A small catalog of config.json and a file to go with it, as /api/config/catalog and /api/config give them. */

import type { CatalogParam, ConfigCatalog, JsonObject } from '@/lib/types'

export function param(path: string, overrides: Partial<CatalogParam> = {}): CatalogParam {
  return {
    path,
    group: 'hours',
    label: path,
    kind: 'int',
    help: `What ${path} does.`,
    access: 'editable',
    reason: null,
    how: null,
    readers: ['trader'],
    consumers: [],
    risk: 'trading',
    effects: [],
    related: [],
    caveats: [],
    also_needs: [],
    unit: null,
    minimum: null,
    maximum: null,
    choices: [],
    optional: false,
    default: null,
    columns: [],
    key_label: null,
    value_label: null,
    rule_default: null,
    ...overrides,
  }
}

export const START = 'trade.rules[market_hours].rule_config.trading_start_hour'
export const END = 'trade.rules[market_hours].rule_config.trading_end_hour'
export const MARKET_ZONE = 'trade.rules[market_hours].rule_config.timezone'
export const NEWS = 'trade.rules[macro_news]'
export const NEWS_BEFORE = 'trade.rules[macro_news].rule_config.minutes_before'
export const STOPLOSS = 'trade.config.position_management.performance_thresholds.stoploss_percent'
export const FRESH = 'trade.config.position_management.wait_for_fresh_quote'
export const LEVEL = 'logging.level'
export const DSN = 'postgresql.dsn'

export const CATALOG: ConfigCatalog = {
  services: [
    { id: 'trader', name: 'Trader', container: 'trader1', profile: null },
    { id: 'position_monitor', name: 'Position monitor', container: 'position_monitor1', profile: null },
    { id: 'scheduler', name: 'Scheduler', container: 'scheduler1', profile: null },
    { id: 'admin', name: 'Admin', container: 'admin1', profile: 'admin' },
  ],
  consumers: [{ id: 'dashboard', name: "The dashboard's limits", live: false }],
  groups: [
    { id: 'hours', title: 'Market hours and calendar', summary: 'When WATA trades.' },
    { id: 'exits', title: 'Exits', summary: 'When a position is sold.' },
    { id: 'reporting', title: 'Reporting and logs', summary: 'What is written.' },
    { id: 'infrastructure', title: 'Infrastructure', summary: 'Set on the server.' },
  ],
  params: [
    param(START, { label: 'Trading starts at', unit: 'h', minimum: 0, maximum: 23, readers: ['trader', 'scheduler'],
      effects: ['The trader takes entries from this hour.'], related: [END] }),
    param(END, { label: 'Trading ends at', unit: 'h', minimum: 0, maximum: 23 }),
    param(MARKET_ZONE, { label: "Market's timezone", kind: 'timezone', optional: true, default: null }),
    param(NEWS, { label: 'No entry around US releases', kind: 'rule', optional: true,
      rule_default: { minutes_before: 5, minutes_after: 15 } }),
    param(NEWS_BEFORE, { label: 'From before a release', unit: 'min', optional: true, default: 5, minimum: 0, maximum: 120 }),
    param('trade.rules[*].rule_name', { label: 'Name of a rule', kind: 'text', readers: [], risk: 'none' }),
    param(STOPLOSS, { group: 'exits', label: 'Stop-loss', kind: 'number', unit: '%', readers: ['position_monitor'] }),
    param(FRESH, { group: 'exits', label: "Judge a new position on Saxo's next quote", kind: 'bool', optional: true,
      default: true, readers: ['position_monitor'] }),
    param(LEVEL, { group: 'reporting', label: 'Log level', kind: 'enum', choices: ['DEBUG', 'INFO', 'WARNING'],
      readers: ['trader', 'position_monitor', 'scheduler', 'admin'], risk: 'none' }),
    param(DSN, { group: 'infrastructure', label: 'Database address', kind: 'secret', access: 'server',
      reason: 'The services connect with it first.', how: 'Edit it on the server.' }),
    param('duckdb.persistant.db_path', { group: 'infrastructure', label: 'DuckDB file', kind: 'text', access: 'unused',
      reason: 'Replaced by PostgreSQL.' }),
  ],
}

export function configFile(): JsonObject {
  return {
    logging: { level: 'INFO' },
    postgresql: { dsn: '********' },
    duckdb: { persistant: { db_path: '/app/var/lib/duckdb/trading_data.duckdb' } },
    trade: {
      rules: [
        { rule_type: 'allowed_indices', rule_name: 'allowed_indices', rule_config: { indice_ids: { us100: '1909050' } } },
        { rule_type: 'market_hours', rule_name: 'market_hours',
          rule_config: { trading_start_hour: 8, trading_end_hour: 22, risky_trading_start_hour: 21, risky_trading_start_minute: 54 } },
      ],
      config: { position_management: { performance_thresholds: { stoploss_percent: -7, max_profit_percent: 60 } } },
    },
  }
}
