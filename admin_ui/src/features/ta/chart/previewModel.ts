// A script drawn on a chart of WATA's bars: one of LuxAlgo's indicators (IndicatorPreviewChart.tsx), or one of the
// operator's as it is being written (its page's Chart tab, ScriptLiveChart.tsx). The assets it can be drawn on, the
// bars offered (a script's: as the rules running it run it first), and what it opens on. Pure.

import { barsLabel } from '@/features/ta/signals/ruleModel'
import type { DataAsset, TaRule, TaRuleBars } from '@/lib/types'

import { assetTicker, fallbackSymbol, testableAssets } from '../chartLinks'
import { parseTicker, velaSymbol } from '../engine/tickers'
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

/** Bars a script is drawn on, as its Chart tab's menu offers them. */
export interface PreviewBars {
  /** What the menu keeps: `R60` for range bars, Pine's timeframe otherwise (`5`). */
  value: string
  label: string
  /** Range bars of this many steps of the asset's price; null for bars of time. */
  rangeTicks: number | null
  /** The timeframe Vela is given: the bars' own, a minute for range bars, which take no length (as the Charts page). */
  timeframe: string
}

function barsOfRule(bars: TaRuleBars): Omit<PreviewBars, 'label'> {
  return bars.type === 'range'
    ? { value: `R${bars.ticks}`, rangeTicks: bars.ticks, timeframe: '1' }
    : { value: bars.timeframe, rangeTicks: null, timeframe: bars.timeframe }
}

/** The bars offered for a script: as each rule running it runs them (once each, the first rule's first), then 1 to 60 minutes. */
export function scriptBars(rules: readonly TaRule[]): PreviewBars[] {
  const runs = new Map<string, { bars: TaRuleBars; names: string[] }>()
  for (const rule of rules) {
    const value = barsOfRule(rule.bars).value
    const found = runs.get(value)
    if (found) found.names.push(rule.name)
    else runs.set(value, { bars: rule.bars, names: [rule.name] })
  }
  const offered: PreviewBars[] = [...runs.values()].map(({ bars, names }) => ({
    ...barsOfRule(bars),
    label: names.length === 1 ? `${barsLabel(bars)}, as “${names[0]}” runs it` : `${barsLabel(bars)}, as ${names.length} rules run it`,
  }))
  for (const entry of PREVIEW_TIMEFRAMES) {
    if (!runs.has(entry.value)) offered.push({ value: entry.value, label: entry.label, rangeTicks: null, timeframe: entry.value })
  }
  return offered
}

/**
 * What a script's chart opens on: its first rule's asset and bars; the Charts page's asset in five minutes otherwise,
 * and when that asset is not offered (a range is counted in an asset's own tick: R60 is 6 points of Capital.com's
 * US100, 0.06 of Dukascopy's).
 */
export function scriptOpening(assets: DataAsset[], rules: readonly TaRule[]): { symbol: string; bars: string } {
  const first = rules[0]
  const fallback = { symbol: previewSymbol(assets, null), bars: PREVIEW_TIMEFRAME }
  if (!first) return fallback
  const own = velaSymbol({ provider: first.asset.provider, key: first.asset.key, side: 'bid', rangeTicks: null })
  return previewSymbol(assets, own) === own ? { symbol: own, bars: barsOfRule(first.bars).value } : fallback
}

/** The symbol and timeframe Vela is given for an asset in these bars: `capital:CAPITAL_US100.R60` and a minute. */
export function previewMarket(symbol: string, bars: PreviewBars): { symbol: string; timeframe: string } {
  const ticker = parseTicker(symbol)
  if (!ticker) return { symbol, timeframe: bars.timeframe }
  return { symbol: velaSymbol({ ...ticker, rangeTicks: bars.rangeTicks }), timeframe: bars.timeframe }
}
