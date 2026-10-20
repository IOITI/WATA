// WATA's assets as Pine and Vela tickers.
//
// PineTS keeps only what follows the colon of a ticker: it asks its provider for
// "US100" when a script reads request.security("SKILLING:US100", …), and takes the
// chart's own prices when that name equals the chart's. So a WATA ticker names its
// feed before the colon is ever dropped: CAPITAL_US100, DUKASCOPY_US100, SAXO_12.
// A suffix asks for a variant: .ASK the ask side (the bid by default, as the asset
// pages show), .R<ticks> range bars of that many steps of the price (TradingView's
// "R"). An exchange prefix is accepted and ignored: CAPITAL:CAPITAL_US100.

export type ProviderId = 'capital' | 'dukascopy' | 'saxo'
export type PriceSide = 'bid' | 'ask'

export const PROVIDERS: ProviderId[] = ['capital', 'dukascopy', 'saxo']

const FEED: Record<ProviderId, string> = { capital: 'CAPITAL', dukascopy: 'DUKASCOPY', saxo: 'SAXO' }

// The keys WATA accepts for each provider (src/admin/data.py), after the feed's name.
const KEY_PATTERN: Record<ProviderId, RegExp> = {
  capital: /^[A-Z0-9][A-Z0-9_.]{0,63}$/,
  dukascopy: /^[a-z0-9][a-z0-9_-]{0,31}$/,
  saxo: /^[1-9][0-9]{0,9}$/,
}

export interface WataTicker {
  provider: ProviderId
  /** The asset's key in WATA: Capital.com's epic, a Dukascopy label, a recorder id. */
  key: string
  side: PriceSide
  /** Range bars of this many steps of the price; null for bars of time. */
  rangeTicks: number | null
}

/** The asset's address in the admin's API: `capital/US100`. */
export function refOf(ticker: Pick<WataTicker, 'provider' | 'key'>): string {
  return `${ticker.provider}/${ticker.key}`
}

/** The ticker without its variant: the asset itself, `CAPITAL_US100`. */
export function baseTicker(ticker: Pick<WataTicker, 'provider' | 'key'>): string {
  const key = ticker.provider === 'dukascopy' ? ticker.key.toUpperCase() : ticker.key
  return `${FEED[ticker.provider]}_${key}`
}

export function tickerText(ticker: WataTicker): string {
  return (
    baseTicker(ticker) + (ticker.side === 'ask' ? '.ASK' : '') + (ticker.rangeTicks ? `.R${ticker.rangeTicks}` : '')
  )
}

/** The symbol Vela loads: the provider's name, then the ticker. */
export function velaSymbol(ticker: WataTicker): string {
  return `${ticker.provider}:${tickerText(ticker)}`
}

/** A WATA ticker, or null when `text` names no asset WATA could hold. */
export function parseTicker(text: string): WataTicker | null {
  let rest = text.trim()
  const colon = rest.lastIndexOf(':')
  if (colon !== -1) rest = rest.slice(colon + 1)
  let side: PriceSide = 'bid'
  let rangeTicks: number | null = null
  // Variants come last, in any order.
  for (;;) {
    const ask = /\.ASK$/i.exec(rest)
    const range = /\.R([0-9]{1,6})$/i.exec(rest)
    if (ask && side === 'bid') {
      side = 'ask'
      rest = rest.slice(0, ask.index)
    } else if (range && rangeTicks === null) {
      rangeTicks = Number(range[1])
      if (rangeTicks < 1) return null
      rest = rest.slice(0, range.index)
    } else {
      break
    }
  }
  for (const provider of PROVIDERS) {
    const prefix = `${FEED[provider]}_`
    if (!rest.toUpperCase().startsWith(prefix)) continue
    const raw = rest.slice(prefix.length)
    const key = provider === 'dukascopy' ? raw.toLowerCase() : provider === 'capital' ? raw.toUpperCase() : raw
    return KEY_PATTERN[provider].test(key) ? { provider, key, side, rangeTicks } : null
  }
  return null
}
