import { createContext, useContext } from 'react'

import type { ConfigCatalog, ConfigDocument, ConfigHistoryEntry, ConfigSaved, JsonObject } from '@/lib/types'

/**
 * The configuration being edited, shared by every view of the Configuration
 * page (its overview, its groups, the JSON, the versions): the draft lives in
 * the page, so that going from one group to another keeps what was changed.
 */
export interface ConfigEditorValue {
  catalog: ConfigCatalog
  doc: ConfigDocument
  draft: JsonObject
  /** Replaces the draft, and the fields that show it (they hold what is being typed). */
  replaceDraft: (next: JsonObject) => void
  /** The catalog paths whose value the draft changed. */
  unsaved: Set<string>
  disabled: boolean
  /** Bumped when the draft is replaced: the fields are keyed by it. */
  formVersion: number
  /** What each save changed, newest first, once read. */
  history: ConfigHistoryEntry[] | undefined
  /** The last save of this page, until dismissed: what it reaches is said above every view. */
  lastSaved: ConfigSaved | null
  dismissSaved: () => void
}

export const ConfigEditorContext = createContext<ConfigEditorValue | null>(null)

export function useConfigEditor(): ConfigEditorValue {
  const context = useContext(ConfigEditorContext)
  if (!context) throw new Error('A view of the configuration must be inside the Configuration page')
  return context
}

export const TIMEZONE = 'trade.config.general.timezone'
export const MARKET_TIMEZONE = 'trade.rules[market_hours].rule_config.timezone'
export const DEFAULT_TIMEZONE = 'Europe/Paris'

/** A name for each service, by its id. */
export function serviceNames(catalog: ConfigCatalog): Map<string, string> {
  return new Map(catalog.services.map((service) => [service.id, service.name]))
}

export type { JsonObject }
