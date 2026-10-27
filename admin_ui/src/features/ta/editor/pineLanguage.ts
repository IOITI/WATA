// Pine Script v5 and v6 for CodeMirror: a line-by-line tokenizer for highlighting,
// its colours (ioiti-ui's theme.css, --code-*), and completion of the built-in functions from
// the TradingView reference the validator carries.

import type { CompletionContext, CompletionResult } from '@codemirror/autocomplete'
import { HighlightStyle, StreamLanguage } from '@codemirror/language'
import type { StreamParser } from '@codemirror/language'
import { tags } from '@lezer/highlight'

const KEYWORDS = new Set([
  'if', 'else', 'for', 'to', 'by', 'in', 'while', 'switch', 'break', 'continue',
  'var', 'varip', 'import', 'export', 'as', 'method', 'type', 'enum', 'and', 'or', 'not',
])
const DECLARATIONS = new Set(['indicator', 'strategy', 'library'])
const TYPES = new Set([
  'int', 'float', 'bool', 'string', 'color', 'line', 'label', 'box', 'table', 'linefill', 'polyline',
  'array', 'matrix', 'map', 'series', 'simple', 'const',
])
const ATOMS = new Set(['true', 'false', 'na'])
export const BUILT_IN_VARIABLES = [
  'open', 'high', 'low', 'close', 'volume', 'hl2', 'hlc3', 'ohlc4', 'hlcc4',
  'time', 'time_close', 'time_tradingday', 'timenow', 'bar_index', 'last_bar_index', 'last_bar_time',
  'year', 'month', 'weekofyear', 'dayofmonth', 'dayofweek', 'hour', 'minute', 'second',
]
const BUILT_IN = new Set(BUILT_IN_VARIABLES)

interface PineState {
  /** The previous token was a dot: what follows is a member, not a name of its own. */
  afterDot: boolean
}

const parser: StreamParser<PineState> = {
  name: 'pine',
  startState: () => ({ afterDot: false }),
  token(stream, state) {
    if (stream.eatSpace()) return null
    const afterDot = state.afterDot
    state.afterDot = false
    // Annotations: //@version=6, // @function, // @param …
    if (stream.match(/^\/\/\s*@[A-Za-z]+/)) {
      stream.skipToEnd()
      return 'meta'
    }
    if (stream.match('//')) {
      stream.skipToEnd()
      return 'comment'
    }
    if (stream.match(/^"(?:[^"\\]|\\.)*"?/) || stream.match(/^'(?:[^'\\]|\\.)*'?/)) return 'string'
    if (stream.match(/^#[0-9A-Fa-f]{6}(?:[0-9A-Fa-f]{2})?\b/)) return 'color'
    if (stream.match(/^(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?/)) return 'number'
    if (stream.match(/^(?::=|=>|==|!=|<=|>=|\+=|-=|\*=|\/=|%=|[-+*/%<>=?:])/)) return 'operator'
    if (stream.eat('.')) {
      state.afterDot = true
      return 'punctuation'
    }
    if (stream.match(/^[A-Za-z_][A-Za-z0-9_]*/)) {
      const word = stream.current()
      const calls = stream.peek() === '('
      if (afterDot) return calls ? 'function' : 'property'
      if (KEYWORDS.has(word)) return 'keyword'
      if (DECLARATIONS.has(word) && calls) return 'declaration'
      if (ATOMS.has(word)) return 'atom'
      if (TYPES.has(word)) return 'typeName'
      if (stream.peek() === '.') return 'namespace'
      if (calls) return 'function'
      if (BUILT_IN.has(word)) return 'builtin'
      return 'variable'
    }
    stream.next()
    return null
  },
  languageData: { commentTokens: { line: '//' } },
  tokenTable: {
    meta: tags.meta,
    comment: tags.lineComment,
    string: tags.string,
    color: tags.color,
    number: tags.number,
    operator: tags.operator,
    punctuation: tags.punctuation,
    keyword: tags.keyword,
    declaration: tags.definitionKeyword,
    atom: tags.atom,
    typeName: tags.typeName,
    namespace: tags.namespace,
    function: tags.function(tags.variableName),
    property: tags.propertyName,
    builtin: tags.standard(tags.variableName),
    variable: tags.variableName,
  },
}

export const pineLanguage = StreamLanguage.define(parser)

export const pineHighlight = HighlightStyle.define([
  { tag: [tags.keyword, tags.definitionKeyword], color: 'var(--code-keyword)', fontWeight: '500' },
  { tag: tags.string, color: 'var(--code-string)' },
  { tag: [tags.number, tags.atom, tags.color], color: 'var(--code-number)' },
  { tag: tags.lineComment, color: 'var(--code-comment)', fontStyle: 'italic' },
  { tag: tags.meta, color: 'var(--code-comment)', fontWeight: '600' },
  { tag: tags.function(tags.variableName), color: 'var(--code-function)' },
  { tag: tags.namespace, color: 'var(--code-namespace)' },
  { tag: tags.typeName, color: 'var(--code-type)' },
  { tag: tags.standard(tags.variableName), color: 'var(--code-builtin)' },
  { tag: [tags.operator, tags.punctuation], color: 'var(--code-operator)' },
])

export interface PineFunction {
  name: string
  signature: string
}

/**
 * Completion of the built-in functions (`ta.sma(source, length)`) and variables,
 * from the word or dotted name being typed. `functions` comes from the TradingView
 * reference the validator carries, loaded with the editor.
 */
export function pineCompletions(functions: PineFunction[]) {
  const options = [
    ...functions.map((fn) => ({ label: fn.name, type: 'function', detail: fn.signature.slice(fn.name.length) })),
    ...BUILT_IN_VARIABLES.map((name) => ({ label: name, type: 'variable' })),
    ...[...KEYWORDS].map((name) => ({ label: name, type: 'keyword' })),
  ]
  return (context: CompletionContext): CompletionResult | null => {
    const word = context.matchBefore(/[A-Za-z_][A-Za-z0-9_.]*/)
    if (!word || (word.from === word.to && !context.explicit)) return null
    return { from: word.from, options, validFor: /^[A-Za-z0-9_.]*$/ }
  }
}
