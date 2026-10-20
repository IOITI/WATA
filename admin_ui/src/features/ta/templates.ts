// What a new script starts from, and the words the pages use for its kind.

import type { TaScriptKind, TaScriptSummary } from '@/lib/types'

export const KIND_LABEL: Record<TaScriptKind, string> = {
  indicator: 'Indicator',
  strategy: 'Strategy',
  library: 'Library',
}

export const TEMPLATES: Record<TaScriptKind, string> = {
  indicator: `//@version=6
indicator("My indicator", overlay = true)

length = input.int(20, "Length", minval = 1)
average = ta.ema(close, length)

plot(average, "Average", color.orange, 2)

// An alert WATA can turn into a signal: its message is the JSON of TradingView's webhook.
alertcondition(ta.crossover(close, average), "Cross up", '{"action": "long", "indice": "us100", "signal_timestamp": "{{time}}", "alert_timestamp": "{{timenow}}"}')
`,
  strategy: `//@version=6
strategy("My strategy", overlay = true, initial_capital = 10000)

fast = ta.ema(close, 9)
slow = ta.ema(close, 21)

if ta.crossover(fast, slow)
    strategy.entry("Long", strategy.long)
if ta.crossunder(fast, slow)
    strategy.close("Long")

plot(fast, "Fast", color.teal)
plot(slow, "Slow", color.orange)
`,
  library: `//@version=6
// @description What this library offers. A script imports it with: import you/MyLibrary/1 as lib
library("MyLibrary")

// @function Twice a value.
export double(float value) =>
    value * 2
`,
}

export function isKind(value: string | null): value is TaScriptKind {
  return value === 'indicator' || value === 'strategy' || value === 'library'
}

/** The name a script declares: indicator("Name"), strategy("Name") or library("Name"). */
export function declaredTitle(source: string): string | null {
  const match = /^(?:indicator|strategy|library)\s*\(\s*(?:title\s*=\s*)?(?:"([^"\n]*)"|'([^'\n]*)')/m.exec(source)
  return match ? (match[1] ?? match[2] ?? '').trim() || null : null
}

/** The kind a source declares, as the server will read it. */
export function declaredKind(source: string): TaScriptKind | null {
  const match = /^(indicator|strategy|library)\s*\(/m.exec(source)
  return match ? (match[1] as TaScriptKind) : null
}

/** The scripts that import each library, by the name the library declares. */
export function importersOf(scripts: TaScriptSummary[]): Map<string, string[]> {
  const importers = new Map<string, string[]>()
  for (const script of scripts) {
    for (const entry of script.imports) {
      importers.set(entry.name, [...(importers.get(entry.name) ?? []), script.name])
    }
  }
  return importers
}
