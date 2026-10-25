// Whether a script's new bars may continue its run, or must run it whole: what it
// reads that a whole run gives otherwise at an earlier bar. The TA engine's runner
// (runnerCore.ts) and the Charts page's worker (pineEngine.ts, vite/velaContinuedRuns.ts)
// decide by the same words. Kept apart from runnerCore.ts, which brings PineTS with it.

import type { PreparedScript } from './prepare'

/** What a script may read that a whole run gives otherwise at an earlier bar, by what its code names. */
const WHOLE_ONLY: RegExp[] = [
  // Where the bars end: barstate.islast, isconfirmed, isrealtime... All but isfirst.
  /\bbarstate\s*\.\s*(?!isfirst\b)[A-Za-z_]\w*/,
  /\blast_bar_(?:index|time)\b/,
  /\bsession\s*\.\s*islastbar(?:_regular)?\b/,
  // The clock, and chance.
  /\btimenow\b/,
  /\bmath\s*\.\s*random\b/,
  // Other series: a run asks them over its own bars.
  /\brequest\s*\.\s*[A-Za-z_]\w*/,
  /\bchart\s*\.\s*(?:left|right)_visible_bar_time\b/,
]

/** A Pine source without its comments, and its strings emptied: the names its code uses. */
export function codeOf(source: string): string {
  let code = ''
  for (let index = 0; index < source.length; index += 1) {
    const char = source[index]
    if (char === '/' && source[index + 1] === '/') {
      const end = source.indexOf('\n', index)
      if (end < 0) break
      index = end - 1
      continue
    }
    if (char === '"' || char === "'") {
      let end = index + 1
      while (end < source.length && source[end] !== char && source[end] !== '\n') end += source[end] === '\\' ? 2 : 1
      code += char + char
      index = end
      continue
    }
    code += char
  }
  return code
}

/** Why each new bar must run a prepared script whole (its libraries inlined in it); null when its run can continue. */
export function wholeBecause(prepared: Pick<PreparedScript, 'source' | 'kind'>): string | null {
  if (prepared.kind === 'strategy') return 'it is a strategy(), whose orders a whole run settles'
  const code = codeOf(prepared.source)
  for (const pattern of WHOLE_ONLY) {
    const found = pattern.exec(code)
    if (found) return `its script reads ${found[0].replace(/\s+/g, '')}`
  }
  return null
}
