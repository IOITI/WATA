// Technical Analysis › Indicators as the page says it, in plain functions: the list narrowed by its address (the
// favourites, a family, the table or the cards), the families offered, a script's picture. The scripts' stars,
// families and pictures are the server's (src/admin/script_details.py): this only reads them.

import type { LuxAlgoFamily, TaScriptSummary } from '@/lib/types'

import { previewImage } from './luxalgo/luxalgoModel'

export const INDICATORS_PATH = '/ta/indicators'
// A family as the server keeps it (MAX_FAMILY of src/admin/script_details.py).
export const MAX_FAMILY = 40

export type IndicatorsView = 'table' | 'cards'

/** What the address narrows the list to: the favourites alone, one family, and how it is shown. */
export interface IndicatorsAddress {
  favourite: 'yes' | null
  family: string | null
  view: IndicatorsView
}

export function readIndicatorsAddress(params: URLSearchParams): IndicatorsAddress {
  const family = (params.get('family') ?? '').trim()
  return {
    favourite: params.get('favourite') === 'yes' ? 'yes' : null,
    family: family ? family.slice(0, MAX_FAMILY) : null,
    view: params.get('view') === 'cards' ? 'cards' : 'table',
  }
}

/** The address of the list narrowed so: what is left at its default is left out. */
export function indicatorsHref(address: IndicatorsAddress): string {
  const params = new URLSearchParams()
  if (address.favourite) params.set('favourite', address.favourite)
  if (address.family) params.set('family', address.family)
  if (address.view === 'cards') params.set('view', 'cards')
  const query = params.toString()
  return query ? `${INDICATORS_PATH}?${query}` : INDICATORS_PATH
}

export const narrowed = (address: IndicatorsAddress) => address.favourite !== null || address.family !== null

const sameFamily = (a: string | null | undefined, b: string | null | undefined) =>
  (a ?? '').toLocaleLowerCase() === (b ?? '').toLocaleLowerCase()

/** The scripts the address keeps: among the favourites, of the family, as the server listed them. */
export function scriptsShown(scripts: readonly TaScriptSummary[], address: IndicatorsAddress): TaScriptSummary[] {
  return scripts.filter(
    (script) => (!address.favourite || Boolean(script.favourite)) && (!address.family || sameFamily(script.family, address.family)),
  )
}

/** The families the scripts have, once each whatever their case, in alphabetical order. */
export function familiesInUse(scripts: readonly Pick<TaScriptSummary, 'family'>[]): string[] {
  const found = new Map<string, string>()
  for (const script of scripts) {
    if (script.family && !found.has(script.family.toLocaleLowerCase())) found.set(script.family.toLocaleLowerCase(), script.family)
  }
  return [...found.values()].sort((a, b) => a.localeCompare(b))
}

/** The families a script may be given: those in use first, then LuxAlgo's Library's not in use, once each. */
export function familiesOffered(scripts: readonly Pick<TaScriptSummary, 'family'>[], luxalgo: readonly LuxAlgoFamily[] = []): string[] {
  const used = familiesInUse(scripts)
  const known = new Set(used.map((family) => family.toLocaleLowerCase()))
  const more = luxalgo.map((family) => family.name).filter((name) => name && !known.has(name.toLocaleLowerCase()))
  return [...used, ...[...new Set(more)].sort((a, b) => a.localeCompare(b))]
}

/** Where a script's picture is loaded from: its own (the admin's API), else LuxAlgo's on its image host; null for none. */
export function pictureSource(script: Pick<TaScriptSummary, 'id' | 'picture' | 'own_picture'>): string | null {
  const picture = script.picture
  if (!picture) return null
  if (script.own_picture) return picture.startsWith(`/api/ta/scripts/${script.id}/picture`) ? picture : null
  return previewImage(picture)
}
