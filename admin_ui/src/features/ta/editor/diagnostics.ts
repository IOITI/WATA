// What the editor marks in a script, from three sources:
// - TradingView's rules (pinescript-v6-validator): wrong parameter names, arity,
//   repainting. It knows v6 only, so a v5 script skips it;
// - PineTS: the script prepared (libraries inlined) and compiled in the worker, its
//   error brought back to the line it was written on;
// - WATA: a ticker that names no asset of WATA, where the run would fail.

import { tokenize } from '../engine/lexer'
import { checkedVersion, originOf, prepareLibraryCheck, prepareScript, PrepareError, SCRIPT_FILE } from '../engine/prepare'
import type { LibraryResolver, PreparedScript } from '../engine/prepare'
import { parseTicker } from '../engine/tickers'
import type { CompileResponse } from '../engine/pineWorkerClient'

export type Severity = 'error' | 'warning' | 'info'

export interface Diagnostic {
  /** 1-based line and column in the script as written. */
  line: number
  column: number
  /** Characters marked from the column; the rest of the line when absent. */
  length?: number
  severity: Severity
  message: string
  source: 'TradingView rules' | 'PineTS' | 'WATA'
}

export interface CheckResult {
  diagnostics: Diagnostic[]
  prepared: PreparedScript | null
  compiled: Extract<CompileResponse, { ok: true }> | null
}

export interface CheckOptions {
  /** A library is checked inside an empty indicator, as PineTS has no library(). */
  libraryName: string | null
  resolve: LibraryResolver
  compile: (source: string) => Promise<CompileResponse>
  /** TradingView's rules; loaded with the editor, absent in tests that leave them out. */
  validate?: (source: string) => { line: number; column: number; length?: number; message: string; severity: number }[]
}

/** The checks' findings in a few words: "2 errors, 1 warning". */
export function checkSummary(diagnostics: Diagnostic[]): string {
  const errors = diagnostics.filter((diagnostic) => diagnostic.severity === 'error').length
  const warnings = diagnostics.filter((diagnostic) => diagnostic.severity === 'warning').length
  if (errors === 0 && warnings === 0) return 'No problem found'
  const parts = []
  if (errors > 0) parts.push(`${errors} error${errors === 1 ? '' : 's'}`)
  if (warnings > 0) parts.push(`${warnings} warning${warnings === 1 ? '' : 's'}`)
  return parts.join(', ')
}

// The validator's advice that a v5 script move to v6: not a fault of the script.
const NOT_FAULTS = [/Recommend using \/\/@version=6/]

/** Strings given where WATA must find an asset: request.security's symbol and input.symbol's default. */
export function tickerChecks(source: string): Diagnostic[] {
  const tokens = tokenize(source).filter((token) => token.kind !== 'space' && token.kind !== 'newline' && token.kind !== 'comment')
  const found: Diagnostic[] = []
  for (let index = 0; index + 4 < tokens.length; index += 1) {
    const [namespace, dot, name, open, argument] = tokens.slice(index, index + 5)
    const call = `${namespace.text}${dot.text}${name.text}`
    if ((call !== 'request.security' && call !== 'input.symbol') || open.text !== '(' || argument.kind !== 'string') continue
    const value = argument.text.slice(1, -1)
    if (value === '' || parseTicker(value)) continue
    found.push({
      line: argument.line,
      column: argument.column,
      length: argument.text.length,
      severity: call === 'request.security' ? 'error' : 'warning',
      message:
        `"${value}" is not one of WATA's assets: name one as CAPITAL_US100, DUKASCOPY_US100 or SAXO_<id> ` +
        '(with .ASK for the ask, .R60 for range bars)',
      source: 'WATA',
    })
  }
  return found
}

function preparedFailure(error: PrepareError): Diagnostic {
  if (error.file === SCRIPT_FILE) {
    return { line: error.line ?? 1, column: error.column ?? 1, severity: 'error', message: error.message, source: 'PineTS' }
  }
  return {
    line: 1,
    column: 1,
    severity: 'error',
    message: `In the library ${error.file}${error.line ? `, line ${error.line}` : ''}: ${error.message}`,
    source: 'PineTS',
  }
}

/**
 * What TradingView's rules (v6 only) and WATA's tickers find in a script, before
 * PineTS compiles it: the editor's first checks, and the server's (check.mjs).
 */
export function ruleFindings(source: string, version: number, validate?: CheckOptions['validate']): Diagnostic[] {
  const diagnostics: Diagnostic[] = []
  if (version === 6 && validate) {
    for (const finding of validate(source)) {
      if (NOT_FAULTS.some((pattern) => pattern.test(finding.message))) continue
      diagnostics.push({
        line: Math.max(1, finding.line),
        column: Math.max(1, finding.column + 1),
        length: finding.length,
        severity: finding.severity === 0 ? 'error' : 'warning',
        message: finding.message,
        source: 'TradingView rules',
      })
    }
  }
  diagnostics.push(...tickerChecks(source))
  return diagnostics
}

export async function checkScript(source: string, options: CheckOptions): Promise<CheckResult> {
  let version: number
  try {
    version = checkedVersion(source)
  } catch (error) {
    if (error instanceof PrepareError) return { diagnostics: [preparedFailure(error)], prepared: null, compiled: null }
    throw error
  }

  const diagnostics = ruleFindings(source, version, options.validate)

  let prepared: PreparedScript
  try {
    prepared = options.libraryName
      ? await prepareLibraryCheck(source, options.libraryName, options.resolve)
      : await prepareScript(source, options.resolve)
  } catch (error) {
    if (error instanceof PrepareError) return { diagnostics: [...diagnostics, preparedFailure(error)], prepared: null, compiled: null }
    throw error
  }
  for (const warning of prepared.warnings) {
    diagnostics.push({ line: 1, column: 1, severity: 'info', message: warning, source: 'WATA' })
  }

  const compiled = await options.compile(prepared.source)
  if (!compiled.ok) {
    const origin = compiled.error.line ? originOf(prepared, compiled.error.line) : null
    const inScript = origin?.file === SCRIPT_FILE && origin.line > 0
    diagnostics.push({
      line: inScript ? origin.line : 1,
      column: inScript ? (compiled.error.column ?? 1) : 1,
      severity: 'error',
      message: origin && !inScript ? `In the library ${origin.file}, line ${origin.line}: ${compiled.error.message}` : compiled.error.message,
      source: 'PineTS',
    })
    return { diagnostics, prepared, compiled: null }
  }
  return { diagnostics, prepared, compiled }
}
