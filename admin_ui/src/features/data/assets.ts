import type { Fact } from '@/features/prices/availability'
import { instrumentLabel, todayIn, valueDecimals } from '@/features/prices/chartData'
import { isDay } from '@/features/prices/view'
import { formatDateTime, formatDay, formatNumber } from '@/lib/format'
import type { AssetSeen, AssetStatus, DataAsset, DataProviderId, PriceInstrument } from '@/lib/types'

/**
 * What the Assets View says of each asset, and the addresses that lead to
 * one: plain functions, tested without a page.
 */

export type AssetType = 'index' | 'long' | 'short'

export const TYPE_LABELS: Record<AssetType, string> = { index: 'Index', long: 'Long turbo', short: 'Short turbo' }
export const PROVIDER_LABELS: Record<DataProviderId, string> = { saxo: 'Saxo', dukascopy: 'Dukascopy' }
export const STATUS_LABELS: Record<AssetStatus, string> = {
  recording: 'Recording',
  ended: 'Ended',
  downloaded: 'Downloaded',
}
export const SEEN_LABELS: Record<AssetSeen, string> = { all: 'All time', '30d': '30 days', '7d': '7 days', today: 'Today' }
export const SEEN: AssetSeen[] = ['all', '30d', '7d', 'today']

export const ASSETS_PATH = '/data/assets'
export const PROVIDERS_PATH = '/data/providers'

export function assetType(asset: Pick<PriceInstrument, 'role' | 'direction'>): AssetType {
  if (asset.role === 'underlying') return 'index'
  return asset.direction === 'short' ? 'short' : 'long'
}

/** An asset as a row of the table: what the search looks through must be among its own fields. */
export interface AssetRow extends DataAsset {
  name: string
  type: AssetType
}

export function assetRows(items: readonly DataAsset[]): AssetRow[] {
  return items.map((item) => ({ ...item, name: instrumentLabel(item), type: assetType(item) }))
}

export function assetHref(asset: Pick<PriceInstrument, 'ref'>): string {
  return `${ASSETS_PATH}/${asset.ref}`
}

/** The day of the trading timezone a moment (epoch seconds) falls on. */
export function dayOf(epoch: number, timeZone: string): string {
  return todayIn(timeZone, new Date(epoch * 1000))
}

/** The columns the table can be filtered on from its address, and the values each takes. */
const FILTERS: Record<string, readonly string[]> = {
  provider: Object.keys(PROVIDER_LABELS),
  type: Object.keys(TYPE_LABELS),
  status: Object.keys(STATUS_LABELS),
}

/**
 * The filters an address asks for (`?provider=saxo`, several values separated
 * by commas): what a link from the Providers page opens the table on. A value
 * the column does not take is left out.
 */
export function tableFilters(params: URLSearchParams): Record<string, string[]> {
  const filters: Record<string, string[]> = {}
  for (const [column, values] of Object.entries(FILTERS)) {
    const asked = (params.get(column) ?? '').split(',').filter((value) => values.includes(value))
    if (asked.length > 0) filters[column] = asked
  }
  return filters
}

/** The address of the Assets View filtered on some columns: `assetsLink({ provider: 'dukascopy' })`. */
export function assetsLink(filters: Partial<Record<'provider' | 'type' | 'status', string>> = {}): string {
  const params = new URLSearchParams()
  for (const [column, value] of Object.entries(filters)) if (value) params.set(column, value)
  const text = params.toString()
  return text ? `${ASSETS_PATH}?${text}` : ASSETS_PATH
}

export function parseSeen(value: string | null): AssetSeen {
  return SEEN.includes(value as AssetSeen) ? (value as AssetSeen) : 'all'
}

/**
 * Where an address of the former Prices page leads now: an instrument to its
 * asset page with the period and the bars it showed, the page alone to the
 * Assets View.
 */
export function legacyPricesTarget(search: string): string {
  const params = new URLSearchParams(search)
  const instrument = Number(params.get('instrument'))
  if (!Number.isInteger(instrument) || instrument <= 0) return ASSETS_PATH
  const kept = new URLSearchParams()
  const from = params.get('from')
  const to = params.get('to')
  if (isDay(from) && isDay(to)) {
    kept.set('from', from)
    kept.set('to', to)
  }
  for (const name of ['interval', 'range', 'side']) {
    const value = params.get(name)
    if (value) kept.set(name, value)
  }
  const text = kept.toString()
  return `${ASSETS_PATH}/saxo/${instrument}${text ? `?${text}` : ''}`
}

function isoOf(epoch: number): string {
  return new Date(epoch * 1000).toISOString()
}

/**
 * The day an asset opens on: that of its last bar, or of its last file for a
 * Dukascopy history, whose last hours of UTC would otherwise open it on two
 * hours of night.
 */
export function openingDay(asset: DataAsset, timeZone: string, today: string): string {
  if (asset.last_day) return asset.last_day
  return asset.last_t === null ? today : dayOf(asset.last_t, timeZone)
}

/**
 * What an opened row says of a Dukascopy history: its last quote, the days
 * fetched and the symbol they are of. A recorded instrument says what the
 * former list of instruments said (instrumentFacts).
 */
export function historyFacts(asset: DataAsset, timeZone: string): Fact[] {
  const facts: Fact[] = []
  if (asset.last_bid !== null && asset.last_t !== null) {
    const decimals = valueDecimals([asset.last_bid, asset.last_ask])
    const quote =
      asset.last_ask === null
        ? formatNumber(asset.last_bid, decimals)
        : `Bid ${formatNumber(asset.last_bid, decimals)} · Ask ${formatNumber(asset.last_ask, decimals)} · spread ${formatNumber(asset.last_ask - asset.last_bid, decimals)}`
    facts.push({ label: 'Last price', value: `${quote} at ${formatDateTime(isoOf(asset.last_t), timeZone)}` })
  }
  facts.push({
    label: 'Fetched',
    value: `${formatNumber(asset.days ?? 0)} day${asset.days === 1 ? '' : 's'}${
      asset.first_day && asset.last_day ? `, from ${formatDay(asset.first_day)} to ${formatDay(asset.last_day)}` : ''
    }`,
  })
  facts.push({ label: 'Instrument', value: [asset.symbol, asset.currency, 'Dukascopy'].filter(Boolean).join(' · ') })
  return facts
}
