// Links into the Charts page: the symbol and timeframe it opens on, and a script
// to add to the chart. Shared by the editor, the Indicators page and the asset
// pages of the Data section.

import type { DataAsset } from '@/lib/types'

import { baseTicker, parseTicker, PROVIDERS, velaSymbol } from './engine/tickers'
import type { ProviderId, WataTicker } from './engine/tickers'
import { timeframeOf } from './engine/timeframes'

export interface ChartsTarget {
  /** A WATA ticker: CAPITAL_US100, DUKASCOPY_US100.R60… */
  symbol?: string
  /** Pine's timeframe: 1, 5, 15, 60. */
  tf?: string
  /** A script to add to the chart. */
  script?: string
}

export function chartsPath(target: ChartsTarget): string {
  const params = new URLSearchParams()
  if (target.symbol) params.set('symbol', target.symbol)
  if (target.tf) params.set('tf', target.tf)
  if (target.script) params.set('script', target.script)
  const search = params.toString()
  return search ? `/ta/charts?${search}` : '/ta/charts'
}

export interface ChartsView {
  ticker: WataTicker | null
  /** Null when the address names none (the saved layout's then). */
  timeframe: string | null
  script: string | null
}

/** What the Charts page's address asks for; what it does not say is left to the saved layout. */
export function chartsView(search: string): ChartsView {
  const params = new URLSearchParams(search)
  const symbol = params.get('symbol')
  const tf = params.get('tf')
  return {
    ticker: symbol ? parseTicker(symbol) : null,
    timeframe: tf && timeframeOf(tf) ? tf : null,
    script: params.get('script'),
  }
}

/** An asset of the Data section as a ticker, when it is of a provider the charts serve. */
export function assetTicker(asset: Pick<DataAsset, 'provider' | 'key'>): WataTicker | null {
  const provider = asset.provider as ProviderId
  if (!PROVIDERS.includes(provider)) return null
  return parseTicker(baseTicker({ provider, key: asset.key }))
}

/** The assets a script can be tried on: the indices and the histories, not the turbos that ended. */
export function testableAssets(assets: DataAsset[]): DataAsset[] {
  return assets.filter((asset) => assetTicker(asset) && !(asset.role === 'turbo' && asset.status === 'ended'))
}

/** What a chart opens on without a word from its address or its layout: the live index first. */
const PREFERENCE: ProviderId[] = ['capital', 'dukascopy', 'saxo']

/** The Vela symbol a chart opens on among the assets: the first index of the preferred provider. */
export function fallbackSymbol(assets: DataAsset[]): string {
  const testable = testableAssets(assets)
  for (const provider of PREFERENCE) {
    const asset = testable.find((candidate) => candidate.provider === provider && candidate.role !== 'turbo')
    const ticker = asset ? assetTicker(asset) : null
    if (ticker) return velaSymbol(ticker)
  }
  return 'capital:CAPITAL_US100'
}

export { velaSymbol }
