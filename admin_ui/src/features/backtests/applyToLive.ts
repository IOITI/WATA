// A run's settings applied to WATA (ApplyToLiveDialog.tsx): what each row of the difference says, which are ticked
// at first, and what is said once saved. The rows come from the server (src/admin/run_to_live.py), which decides what
// can be applied; this module only says it. Pure.

import type { BacktestField, BacktestValue, RunSettingDifference } from '@/lib/types'

import { showValue } from './model'

/** A row's value as the run page says the run's settings: a unit after a number, On or Off, the releases listed. */
export function differenceValue(value: RunSettingDifference['before'], row: Pick<RunSettingDifference, 'unit'>,
  field?: Pick<BacktestField, 'kind' | 'unit'>): string {
  if (Array.isArray(value)) return value.length > 0 ? value.join(', ') : 'None'
  return showValue(value as BacktestValue, field ?? { kind: 'number', unit: row.unit })
}

/** Whose the run's value is: the run changed it, or it was WATA's own on the day the run was asked. */
export function differenceTag(row: Pick<RunSettingDifference, 'own'>): string {
  return row.own ? 'Changed by this run' : 'WATA’s then, changed since'
}

/** Ticked at first: every row that can be applied, so that WATA trades as the run did. */
export function initiallyTicked(rows: readonly RunSettingDifference[]): string[] {
  return rows.filter((row) => row.apply).map((row) => row.setting)
}

/** Why the review cannot be asked for, or null. */
export function reviewBlocked(ticked: readonly string[], editable: boolean): string | null {
  if (!editable) return 'Configuration editing is switched off (admin.config_edit_enabled): set it on the server to save from here.'
  if (ticked.length === 0) return 'Tick at least one setting to apply.'
  return null
}

/** What the dialog says of the rows, above them. */
export function differencesSaid(rows: readonly RunSettingDifference[]): string {
  if (rows.length === 0) return 'WATA already trades with this run’s settings.'
  const own = rows.filter((row) => row.own).length
  const since = rows.length - own
  const parts = [
    own > 0 ? `${own} the run changed` : null,
    since > 0 ? `${since} WATA has changed since the run was asked` : null,
  ].filter(Boolean)
  return `${rows.length} setting${rows.length === 1 ? ' differs' : 's differ'} from WATA’s now: ${parts.join(', ')}.`
}
