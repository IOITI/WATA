// A Pine Script lexer: enough of the language to find statements, names and
// imports without running anything. Used to inline libraries (prepare.ts); the
// editor's highlighting has its own line-by-line tokenizer (pineLanguage.ts).

export type TokenKind =
  | 'identifier'
  | 'number'
  | 'string'
  | 'color'
  | 'operator'
  | 'comment'
  | 'newline'
  | 'space'

export interface Token {
  kind: TokenKind
  text: string
  /** Offset of the first character in the source. */
  start: number
  /** 1-based line and column of the first character. */
  line: number
  column: number
}

const OPERATORS = [':=', '=>', '==', '!=', '<=', '>=', '+=', '-=', '*=', '/=', '%=']

function isIdentifierStart(char: string): boolean {
  return /[A-Za-z_]/.test(char)
}

function isIdentifierPart(char: string): boolean {
  return /[A-Za-z0-9_]/.test(char)
}

/** Every token of ``source``, spaces, comments and newlines included, in order. */
export function tokenize(source: string): Token[] {
  const tokens: Token[] = []
  let index = 0
  let line = 1
  let lineStart = 0
  const push = (kind: TokenKind, end: number) => {
    tokens.push({ kind, text: source.slice(index, end), start: index, line, column: index - lineStart + 1 })
    index = end
  }
  while (index < source.length) {
    const char = source[index]
    if (char === '\n') {
      push('newline', index + 1)
      line += 1
      lineStart = index
      continue
    }
    if (char === '\r' || char === ' ' || char === '\t') {
      let end = index + 1
      while (end < source.length && (source[end] === ' ' || source[end] === '\t' || source[end] === '\r')) end += 1
      push('space', end)
      continue
    }
    if (source.startsWith('//', index)) {
      const newline = source.indexOf('\n', index)
      push('comment', newline === -1 ? source.length : newline)
      continue
    }
    if (char === '"' || char === "'") {
      let end = index + 1
      while (end < source.length && source[end] !== char && source[end] !== '\n') {
        end += source[end] === '\\' && source[end + 1] !== '\n' ? 2 : 1
      }
      // An unclosed string ends with its line, the newline left to the next token.
      const closed = end < source.length && source[end] === char
      push('string', closed ? end + 1 : Math.min(end, source.length))
      continue
    }
    if (char === '#' && /[0-9A-Fa-f]/.test(source[index + 1] ?? '')) {
      let end = index + 1
      while (end < source.length && /[0-9A-Fa-f]/.test(source[end])) end += 1
      push('color', end)
      continue
    }
    if (/[0-9]/.test(char) || (char === '.' && /[0-9]/.test(source[index + 1] ?? ''))) {
      const match = /^(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?/.exec(source.slice(index))
      push('number', index + (match ? match[0].length : 1))
      continue
    }
    if (isIdentifierStart(char)) {
      let end = index + 1
      while (end < source.length && isIdentifierPart(source[end])) end += 1
      push('identifier', end)
      continue
    }
    const operator = OPERATORS.find((candidate) => source.startsWith(candidate, index))
    push('operator', index + (operator ? operator.length : 1))
  }
  return tokens
}

/** The tokens that carry meaning: no spaces, comments or newlines. */
export function significant(tokens: Token[]): Token[] {
  return tokens.filter((token) => token.kind !== 'space' && token.kind !== 'comment' && token.kind !== 'newline')
}

/** The source split in lines, each with its tokens (the newline itself left out). */
export function linesOf(tokens: Token[]): Token[][] {
  const lines: Token[][] = [[]]
  for (const token of tokens) {
    if (token.kind === 'newline') lines.push([])
    else lines[lines.length - 1].push(token)
  }
  return lines
}
