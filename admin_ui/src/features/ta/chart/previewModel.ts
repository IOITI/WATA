// One of LuxAlgo's indicators on a chart of WATA's bars (IndicatorPreviewChart.tsx): the assets it can be drawn on,
// the lengths of bars offered, and which asset it opens on. Pure.

import type { DataAsset } from '@/lib/types'

import { assetTicker, fallbackSymbol, testableAssets } from '../chartLinks'
import { velaSymbol } from '../engine/tickers'
import { PROVIDER_LABEL } from './wataProvider'

/** The lengths of bars offered, in Pine's minutes. */
export const PREVIEW_TIMEFRAMES = [
  { value: '1', label: '1 minute' },
  { value: '5', label: '5 minutes' },
  { value: '15', label: '15 minutes' },
  { value: '60', label: '1 hour' },
]
export const PREVIEW_TIMEFRAME = '5'

export interface PreviewAsset {
  /** Vela's symbol: `capital:CAPITAL_US100`. */
  symbol: string
  label: string
}

/** The indices and histories an indicator can be drawn on, each with its Vela symbol: no turbo. */
export function previewAssets(assets: DataAsset[]): PreviewAsset[] {
  return testableAssets(assets)
    .filter((asset) => asset.role !== 'turbo')
    .flatMap((asset) => {
      const ticker = assetTicker(asset)
      if (!ticker) return []
      const name = asset.description || asset.symbol || asset.key
      return [{ symbol: velaSymbol(ticker), label: `${name} · ${PROVIDER_LABEL[ticker.provider]}` }]
    })
}

/** The asset chosen when it is offered, else the one the Charts page opens on. */
export function previewSymbol(assets: DataAsset[], chosen: string | null): string {
  if (chosen && previewAssets(assets).some((asset) => asset.symbol === chosen)) return chosen
  return fallbackSymbol(assets)
}
