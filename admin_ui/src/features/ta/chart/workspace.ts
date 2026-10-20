// What the Vela workspace of the Charts page is given besides its data providers:
// - its indicator list: WATA's indicators and strategies, prepared (libraries
//   inlined), so that they appear in Vela's own picker;
// - its layout, saved by the admin (iPhone and Mac see the same), read before the
//   workspace is built so that it is applied at once, with the symbol and
//   timeframe of the page's address over it.

import type { ResolvedIndicator, VelaStorage } from '@luxalgo/vela/widget'
import { decodeState, encodeState } from '@luxalgo/vela/workspace'

import { api } from '@/lib/api'
import type { TaScript, TaScriptSummary } from '@/lib/types'

import { prepareScript } from '../engine/prepare'
import type { LibraryResolver } from '../engine/prepare'

/** Where the Charts page keeps its layout (src/admin/ta_scripts.py, workspaces/). */
export const LAYOUT_KEY = 'charts'

export const CATEGORY: Record<'indicator' | 'strategy', string> = {
  indicator: 'WATA indicators',
  strategy: 'WATA strategies',
}

export interface ScriptEntry {
  id: string
  entry: ResolvedIndicator
}

export interface ManifestSources {
  list: () => Promise<{ items: TaScriptSummary[] }>
  read: (id: string) => Promise<TaScript>
}

/**
 * WATA's indicators and strategies as manifest entries, named as the scripts are:
 * a saved layout records an indicator by that name and its changed inputs, so a
 * chart opened again runs each script's current version. A script that does not
 * prepare (an import missing) is left out, the reason kept for the page to say.
 */
export async function scriptEntries(
  resolve: LibraryResolver,
  sources: ManifestSources = { list: api.taScripts, read: api.taScript },
): Promise<{ entries: ScriptEntry[]; skipped: { name: string; reason: string }[] }> {
  const { items } = await sources.list()
  const entries: ScriptEntry[] = []
  const skipped: { name: string; reason: string }[] = []
  for (const summary of items) {
    if (summary.kind === 'library') continue
    try {
      const script = await sources.read(summary.id)
      const prepared = await prepareScript(script.source, resolve)
      entries.push({
        id: summary.id,
        entry: { name: summary.name, script: prepared.source, enabled: false, category: CATEGORY[summary.kind] },
      })
    } catch (error) {
      skipped.push({ name: summary.name, reason: error instanceof Error ? error.message : String(error) })
    }
  }
  return { entries, skipped }
}

/** The saved layout with the address's symbol and timeframe over its chart, or null to start afresh. */
export function layoutFor(saved: string | null, target: { symbol?: string; timeframe?: string }): string | null {
  if (!saved) return null
  const state = decodeState(saved)
  if (!state) return null
  const first = state.charts[0]
  if (first) {
    if (target.symbol) {
      first.symbol = target.symbol
      delete first.provider
    }
    if (target.timeframe) first.timeframe = target.timeframe
  }
  return encodeState(state)
}

/**
 * Vela's storage: the layout read beforehand answers at once (a synchronous
 * answer is applied before the first paint), each change is saved by the admin.
 */
export function layoutStorage(
  initial: string | null,
  save: (key: string, value: string) => Promise<void> = api.saveTaWorkspace,
): VelaStorage {
  return {
    get: (key) => (key === LAYOUT_KEY ? initial : null),
    set: (key, value) => {
      // Fire and forget, as Vela expects: a failed save leaves the previous layout.
      save(key, value).catch(() => undefined)
    },
  }
}
