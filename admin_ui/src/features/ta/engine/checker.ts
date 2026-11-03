/// <reference types="node" />
// The server's check of a script against TradingView's rules (pinescript-v6-validator,
// as the editor runs them) and WATA's tickers: what Claude's pine_check adds to
// PineTS's compile (src/research/study.py); and its rule check, what keeps it from
// being a signal rule as written (ruleCheck.ts, src/research/rule_check.py). One
// Node process, started as `node --permission --allow-fs-read=<its folder> check.mjs`
// with no environment, confined as the Pine runner is; it runs no script, it only
// reads one. Lines of JSON:
//
//   study → checker   {"id": 1, "op": "hello"}                 its versions
//   study → checker   {"id": 2, "op": "validate", "source": ...}
//   checker → study   {"id": 2, "ok": true, "op": "validate", "diagnostics": [...]}
//   study → checker   {"id": 3, "op": "rule_check", "source": ..., "libraries": [{"id", "name", "version", "source"}]}
//   checker → study   {"id": 3, "ok": true, "op": "rule_check", "check": {...}, "tradingview_rules": [...], "probe": "..." | null}
//
// Built into src/ta_engine/checker/check.mjs by `npm run build:checker`, apart
// from the engine's runner.mjs, which never carries it.

import { registerHooks } from 'node:module'
import { createInterface } from 'node:readline'

import { validatePineScript } from 'pinescript-v6-validator'

import { ruleFindings } from '../editor/diagnostics'
import { checkedVersion, prepareScript, PrepareError } from './prepare'
import type { LibrarySource } from './prepare'
import { liveProbe, RULE_CHECK_VERSION, ruleCheckOf } from './ruleCheck'

declare const __VALIDATOR_VERSION__: string

registerHooks({
  resolve(specifier) {
    throw new Error(`The checker imports nothing: ${specifier}`)
  },
})
for (const name of ['fetch', 'WebSocket', 'EventSource', 'XMLHttpRequest']) {
  Reflect.deleteProperty(globalThis, name)
}
for (const name of [
  'getBuiltinModule', 'binding', '_linkedBinding', 'dlopen', 'kill', 'chdir',
  'setuid', 'setgid', 'seteuid', 'setegid', 'setgroups', 'initgroups',
]) {
  Object.defineProperty(process, name, { value: undefined, configurable: false, writable: false })
}

const write = process.stdout.write.bind(process.stdout)
const send = (message: unknown) => {
  write(`${JSON.stringify(message)}\n`)
}

interface CheckRequest {
  id: number
  op: string
  source?: unknown
  libraries?: unknown
}

/** The libraries a rule check is given, by the name each declares: what its script may import. */
function librariesOf(given: unknown): Map<string, LibrarySource> {
  const found = new Map<string, LibrarySource>()
  for (const entry of Array.isArray(given) ? given : []) {
    const library = entry as Partial<LibrarySource>
    if (typeof library.name === 'string' && typeof library.source === 'string') {
      found.set(library.name, { id: String(library.id ?? library.name), name: library.name, version: Number(library.version ?? 0), source: library.source })
    }
  }
  return found
}

async function ruleCheck(request: CheckRequest & { source: string }): Promise<unknown> {
  const libraries = librariesOf(request.libraries)
  let rules: unknown[] = []
  try {
    rules = ruleFindings(request.source, checkedVersion(request.source), validatePineScript)
  } catch (error) {
    if (!(error instanceof PrepareError)) throw error
  }
  try {
    const prepared = await prepareScript(request.source, async (name) => libraries.get(name) ?? null)
    return { id: request.id, ok: true, op: 'rule_check', check: ruleCheckOf(request.source, prepared), tradingview_rules: rules,
      probe: liveProbe(request.source) }
  } catch (error) {
    if (!(error instanceof PrepareError)) throw error
    return { id: request.id, ok: true, op: 'rule_check', check: ruleCheckOf(request.source, null, error), tradingview_rules: rules }
  }
}

export async function answer(request: CheckRequest): Promise<unknown> {
  if (request.op === 'hello') {
    return { id: request.id, ok: true, op: 'hello', validator: __VALIDATOR_VERSION__, rule_check: RULE_CHECK_VERSION, node: process.version }
  }
  if ((request.op !== 'validate' && request.op !== 'rule_check') || typeof request.source !== 'string') {
    return { id: request.id, ok: false, error: { message: 'A request is hello, or validate or rule_check with a source', file: null, line: null, column: null } }
  }
  if (request.op === 'rule_check') {
    try {
      return await ruleCheck({ ...request, source: request.source })
    } catch (error) {
      return { id: request.id, ok: false, error: { message: String(error), file: null, line: null, column: null } }
    }
  }
  try {
    const version = checkedVersion(request.source)
    return { id: request.id, ok: true, op: 'validate', version, diagnostics: ruleFindings(request.source, version, validatePineScript) }
  } catch (error) {
    if (error instanceof PrepareError) {
      return { id: request.id, ok: false, error: { message: error.message, file: error.file, line: error.line, column: error.column } }
    }
    return { id: request.id, ok: false, error: { message: String(error), file: null, line: null, column: null } }
  }
}

const lines = createInterface({ input: process.stdin, crlfDelay: Infinity })
lines.on('line', (line) => {
  let request: CheckRequest
  try {
    request = JSON.parse(line) as CheckRequest
  } catch {
    send({ id: null, ok: false, error: { message: 'A line that is not JSON', file: null, line: null, column: null } })
    return
  }
  // The study asks one thing at a time: its answers go back in the order asked.
  void answer(request).then(send)
})
lines.on('close', () => process.exit(0))
