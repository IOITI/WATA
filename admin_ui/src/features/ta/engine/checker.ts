/// <reference types="node" />
// The server's check of a script against TradingView's rules (pinescript-v6-validator,
// as the editor runs them) and WATA's tickers: what Claude's pine_check adds to
// PineTS's compile (src/research/study.py). One Node process, started as
// `node --permission --allow-fs-read=<its folder> check.mjs` with no environment,
// confined as the Pine runner is; it runs no script, it only reads one. Lines of JSON:
//
//   study → checker   {"id": 1, "op": "hello"}                 its versions
//   study → checker   {"id": 2, "op": "validate", "source": ...}
//   checker → study   {"id": 2, "ok": true, "op": "validate", "diagnostics": [...]}
//
// Built into src/ta_engine/checker/check.mjs by `npm run build:checker`, apart
// from the engine's runner.mjs, which never carries it.

import { registerHooks } from 'node:module'
import { createInterface } from 'node:readline'

import { validatePineScript } from 'pinescript-v6-validator'

import { ruleFindings } from '../editor/diagnostics'
import { checkedVersion, PrepareError } from './prepare'

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
}

export function answer(request: CheckRequest): unknown {
  if (request.op === 'hello') return { id: request.id, ok: true, op: 'hello', validator: __VALIDATOR_VERSION__, node: process.version }
  if (request.op !== 'validate' || typeof request.source !== 'string') {
    return { id: request.id, ok: false, error: { message: 'A request is hello, or validate with a source', file: null, line: null, column: null } }
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
  send(answer(request))
})
lines.on('close', () => process.exit(0))
