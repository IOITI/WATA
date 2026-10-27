// What the Signals page says of the sources of the signals (trade.config.signal_sources), from what the web
// server runs with: each index set to the TA engine, whose live rule trades it alone, and the TradingView
// alerts it answered without keeping since it started. An index absent takes TradingView's alerts, as
// before, and is not said. Pure.

import { formatDateTime } from 'ioiti-ui'

import type { WebhookState } from '@/lib/types'

export interface SourceNote {
  indice: string
  text: string
}

export function sourceNotes(webhook: WebhookState | null | undefined, timeZone: string): SourceNote[] {
  if (!webhook) return []
  return Object.entries(webhook.sources)
    .filter(([, source]) => source === 'engine')
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([indice]) => {
      const ignored = webhook.ignored[indice]
      const heard = ignored
        ? ` ${ignored.count} ${ignored.count === 1 ? 'alert' : 'alerts'} of TradingView ignored since ${formatDateTime(ignored.first_at, timeZone)}, the last at ${formatDateTime(ignored.last_at, timeZone)}${ignored.last_action ? ` (${ignored.last_action})` : ''}: switch it off in TradingView.`
        : ' No alert of TradingView has come for it since the web server started.'
      return {
        indice,
        text: `${indice} takes its signals from the TA engine’s live rule alone; TradingView’s alerts for it are answered and not kept.${heard}`,
      }
    })
}
