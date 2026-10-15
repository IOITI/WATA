import type {
  CapitalSettings,
  CatalogEntry,
  DataProvider,
  DataProviders,
  JsonObject,
  JsonValue,
  RecorderSettings,
} from '@/lib/types'

/**
 * What the Providers pages say and check, as plain functions tested without
 * a page: which providers are listed and which are offered to be switched
 * on, and what the price recorder's dialog writes back, refuses and warns of.
 */

export const KIND_LABELS: Record<CatalogEntry['kind'], string> = {
  live: 'Live stream',
  download: 'Downloaded history',
}

export const PROVIDER_NAMES: Record<string, string> = {
  saxo: 'Saxo Bank',
  dukascopy: 'Dukascopy',
  capital: 'Capital.com',
}

/** The providers on the page: switched on, or holding data. The others are offered by "Add provider". */
export function listedProviders(answer: DataProviders | undefined): DataProvider[] {
  return (answer?.items ?? []).filter((provider) => provider.active)
}

export type CatalogState = 'active' | 'available' | 'coming'

export interface CatalogItem {
  entry: CatalogEntry
  state: CatalogState
  provider?: DataProvider
}

/** Every provider WATA has code for, or will: what "Add provider" offers, and in what state each is. */
export function catalogItems(answer: DataProviders | undefined): CatalogItem[] {
  return (answer?.catalog ?? []).map((entry) => {
    const provider = answer?.items.find((item) => item.id === entry.id)
    const state: CatalogState = !entry.available ? 'coming' : provider?.active ? 'active' : 'available'
    return { entry, state, provider }
  })
}

// ── the price recorder's settings ──

export const RECORDER_KEYS = [
  'enabled',
  'record_volume',
  'max_instruments',
  'refresh_interval_seconds',
  'refresh_rate_ms',
  'retention_months',
  'alert_after_seconds',
  'instruments',
] as const

const same = (a: JsonValue | undefined, b: JsonValue | undefined) => JSON.stringify(a) === JSON.stringify(b)

/**
 * What a dialog shows: the section as it is on disk, each setting it leaves
 * out at its default. A number field with nothing in it could not be saved.
 */
function draftOf(section: JsonObject, defaults: object, keys: readonly string[]): JsonObject {
  const known = defaults as Record<string, unknown>
  const draft: JsonObject = {}
  for (const key of keys) {
    draft[key] = (key in section ? section[key] : known[key]) as JsonValue
  }
  return draft
}

/**
 * The section written back: the one on disk, with the settings the dialog
 * changed. A setting the file leaves out stays out while it keeps its default:
 * the file says what someone chose, and a default changed in a later version
 * still applies to it. Anything the dialog does not know of is kept.
 */
function sectionWith(onDisk: JsonObject, draft: JsonObject, defaults: object, keys: readonly string[]): JsonObject {
  const known = defaults as Record<string, unknown>
  const saved: JsonObject = { ...onDisk }
  for (const key of keys) {
    if (key in onDisk || !same(draft[key], known[key] as JsonValue)) saved[key] = draft[key]
  }
  return saved
}

export function recorderDraft(section: JsonObject, defaults: RecorderSettings): JsonObject {
  return draftOf(section, defaults, RECORDER_KEYS)
}

export function sectionToSave(onDisk: JsonObject, draft: JsonObject, defaults: RecorderSettings): JsonObject {
  return sectionWith(onDisk, draft, defaults, RECORDER_KEYS)
}

/**
 * What is wrong with the instruments typed, refused before anything is sent:
 * two under one label, or one twice, would each be recorded as an index of
 * their own.
 */
export function instrumentProblems(instruments: JsonValue | undefined): string[] {
  if (!Array.isArray(instruments)) return []
  const problems: string[] = []
  const labels = new Set<string>()
  const keys = new Set<string>()
  instruments.forEach((entry, index) => {
    const row = `Instrument ${index + 1}`
    if (typeof entry !== 'object' || entry === null || Array.isArray(entry)) {
      problems.push(`${row} is not an instrument`)
      return
    }
    const { label, uic, asset_type: assetType } = entry
    if (typeof label !== 'string' || label.trim() === '') problems.push(`${row} needs a label`)
    if (typeof uic !== 'number' || !Number.isInteger(uic) || uic <= 0) problems.push(`${row} needs a UIC, a whole number`)
    if (typeof assetType !== 'string' || assetType.trim() === '') problems.push(`${row} needs an asset type`)
    if (typeof label === 'string' && label.trim() !== '') {
      if (labels.has(label)) problems.push(`Two instruments are labelled "${label}"`)
      labels.add(label)
    }
    if (typeof uic === 'number' && typeof assetType === 'string') {
      const key = `${uic} ${assetType}`
      if (keys.has(key)) problems.push(`UIC ${uic} as ${assetType} is listed twice`)
      keys.add(key)
    }
  })
  return problems
}

function labelsOf(instruments: JsonValue | undefined): Map<string, string> {
  const found = new Map<string, string>()
  if (!Array.isArray(instruments)) return found
  for (const entry of instruments) {
    if (typeof entry === 'object' && entry !== null && !Array.isArray(entry) && typeof entry.label === 'string') {
      found.set(entry.label, JSON.stringify(entry))
    }
  }
  return found
}

/**
 * What a save would do that cannot be taken back, or does more than it says:
 * said in the review, before the slide that saves.
 */
export function recorderWarnings(before: JsonObject, after: JsonObject, indices: readonly string[]): string[] {
  const warnings: string[] = []
  if (before.enabled === true && after.enabled === false) {
    warnings.push(
      'The recorder stops at its next start: the prices of the time it is off can never be fetched afterwards.',
    )
  }
  const kept = typeof after.retention_months === 'number' ? after.retention_months : 0
  const keptBefore = typeof before.retention_months === 'number' ? before.retention_months : 0
  if (kept > 0 && (keptBefore === 0 || kept < keptBefore)) {
    warnings.push(
      `Only ${kept} month${kept === 1 ? '' : 's'} kept: the bars of the months before, their coverage and their volume are deleted at the recorder's next refresh, for good.`,
    )
  }
  const beforeLabels = labelsOf(before.instruments)
  for (const [label, entry] of labelsOf(after.instruments)) {
    if (indices.includes(label) && beforeLabels.get(label) !== entry) {
      warnings.push(`"${label}" is the name of an indice: this instrument is recorded as its index, in place of the one the recorder finds itself.`)
    }
  }
  return warnings
}

// ── the Capital.com feed's settings ──

export const CAPITAL_KEYS = [
  'enabled',
  'environment',
  'instruments',
  'minute_volume',
  'day_files',
  'retention_months',
  'alert_after_seconds',
] as const

/** Capital.com's limit of instruments in one subscription of its stream. */
export const CAPITAL_MAX_INSTRUMENTS = 40
const EPIC = /^[A-Z0-9][A-Z0-9_.]{0,63}$/
const LABEL = /^[a-z0-9][a-z0-9_-]{0,31}$/

export function capitalDraft(section: JsonObject, defaults: CapitalSettings): JsonObject {
  return draftOf(section, defaults, CAPITAL_KEYS)
}

export function capitalSectionToSave(onDisk: JsonObject, draft: JsonObject, defaults: CapitalSettings): JsonObject {
  return sectionWith(onDisk, draft, defaults, CAPITAL_KEYS)
}

function entries(instruments: JsonValue | undefined): { epic: unknown; label: unknown }[] {
  if (!Array.isArray(instruments)) return []
  return instruments.map((entry) =>
    typeof entry === 'object' && entry !== null && !Array.isArray(entry)
      ? { epic: entry.epic, label: entry.label }
      : { epic: undefined, label: undefined },
  )
}

/**
 * What is wrong with the instruments typed, refused before anything is sent,
 * as the feed would refuse it at its start: an epic of Capital.com, a label
 * of WATA's, each once.
 */
export function capitalInstrumentProblems(instruments: JsonValue | undefined): string[] {
  const rows = entries(instruments)
  const problems: string[] = []
  if (rows.length === 0) problems.push('At least one instrument: the Nasdaq is US100, labelled us100')
  if (rows.length > CAPITAL_MAX_INSTRUMENTS) problems.push(`${CAPITAL_MAX_INSTRUMENTS} instruments at most: Capital.com's limit`)
  const epics = new Set<string>()
  const labels = new Set<string>()
  rows.forEach(({ epic, label }, index) => {
    const row = `Instrument ${index + 1}`
    if (typeof epic !== 'string' || !EPIC.test(epic)) problems.push(`${row} needs an epic, Capital.com's name of it in capitals (US100)`)
    if (typeof label !== 'string' || !LABEL.test(label))
      problems.push(`${row} needs a label: lower-case letters, digits, - or _ (us100)`)
    if (typeof epic === 'string' && epic !== '') {
      if (epics.has(epic)) problems.push(`${epic} is listed twice`)
      epics.add(epic)
    }
    if (typeof label === 'string' && label !== '') {
      if (labels.has(label)) problems.push(`Two instruments are labelled "${label}"`)
      labels.add(label)
    }
  })
  return problems
}

function labelsByEpic(instruments: JsonValue | undefined): Map<string, string> {
  const found = new Map<string, string>()
  for (const { epic, label } of entries(instruments)) {
    if (typeof epic === 'string' && typeof label === 'string') found.set(epic, label)
  }
  return found
}

/** What a save would do that cannot be taken back, or does more than it says: said in the review. */
export function capitalWarnings(before: JsonObject, after: JsonObject): string[] {
  const warnings: string[] = []
  if (before.enabled === true && after.enabled === false) {
    warnings.push('The feed stops at its next start: the prices of the time it is off can never be fetched afterwards.')
  }
  if (before.environment !== after.environment && after.environment === 'live') {
    warnings.push('The key logs into the live account at the feed’s next start: it must be a key of that account.')
  }
  const kept = typeof after.retention_months === 'number' ? after.retention_months : 0
  const keptBefore = typeof before.retention_months === 'number' ? before.retention_months : 0
  if (kept > 0 && (keptBefore === 0 || kept < keptBefore)) {
    warnings.push(
      `Only ${kept} month${kept === 1 ? '' : 's'} kept in the database: the bars of the months before, their coverage and Capital.com's minutes are deleted at the feed's next start, for good. The day files stay.`,
    )
  }
  const beforeLabels = labelsByEpic(before.instruments)
  const afterLabels = labelsByEpic(after.instruments)
  for (const [epic, label] of beforeLabels) {
    const next = afterLabels.get(epic)
    if (next === undefined) warnings.push(`${epic} is no longer recorded from the feed's next start: what was recorded of it stays.`)
    else if (next !== label)
      warnings.push(`The day files of ${epic} go under "${next}" from the feed's next start; those written stay under "${label}".`)
  }
  return warnings
}

