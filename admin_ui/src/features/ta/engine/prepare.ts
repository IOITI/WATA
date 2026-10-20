// What every Pine script goes through before PineTS sees it, in the browser and
// on the server alike.
//
// - Only native Pine of version 5 or 6: PineTS runs a source without //@version,
//   or one marked //@PineTS, as JavaScript.
// - Libraries are inlined. PineTS has no `import` yet ("planned"), so
//   `import user/Name/1 as alias` is replaced by the code of the WATA library that
//   declares library("Name"): each name it declares at its top level becomes
//   `Name__name`, and each `alias.name` of the script the same. A library's own
//   imports come first, each library once.
//
// What cannot be inlined safely is refused with a message, never guessed:
// methods, and a library name also used for a parameter or a local variable.

import { linesOf, significant, tokenize } from './lexer'
import type { Token } from './lexer'

export type ScriptKind = 'indicator' | 'strategy' | 'library'

export interface LibrarySource {
  id: string
  name: string
  version: number
  source: string
}

/** The WATA library declared as library("<name>"), or null when there is none. */
export type LibraryResolver = (name: string) => Promise<LibrarySource | null>

/** Where a line of the prepared source comes from: the script, or a library by name. */
export interface LineOrigin {
  file: string
  line: number
}

export interface PreparedScript {
  source: string
  pineVersion: number
  kind: ScriptKind
  title: string | null
  libraries: { id: string; name: string; version: number }[]
  lineMap: LineOrigin[]
  warnings: string[]
}

export const SCRIPT_FILE = 'script'

export class PrepareError extends Error {
  readonly file: string
  readonly line: number | null
  readonly column: number | null

  constructor(message: string, file: string, line: number | null = null, column: number | null = null) {
    super(message)
    this.name = 'PrepareError'
    this.file = file
    this.line = line
    this.column = column
  }
}

const VERSION_LINE = /^\s*\/\/\s*@version\s*=\s*(\d+)\s*$/m
const PINETS_MARKER = /^\s*\/\/\s*@PineTS\b/m
const SUPPORTED_VERSIONS = [5, 6]
const DECLARATIONS = ['indicator', 'strategy', 'library']
// What may stand before a variable's name in its declaration.
const DECLARATION_WORDS = new Set([
  'var', 'varip', 'const', 'simple', 'series', 'input',
  'int', 'float', 'bool', 'string', 'color', 'line', 'label', 'box', 'table', 'linefill', 'polyline',
  'array', 'matrix', 'map', 'chart', 'point',
])

interface Statement {
  /** Index in the source's lines (0-based) of the first and last line. */
  first: number
  last: number
}

/** The Pine version of a native source, or the reason it cannot go to PineTS. */
export function checkedVersion(source: string, file: string = SCRIPT_FILE): number {
  if (!source.trim()) throw new PrepareError('The script is empty', file)
  if (PINETS_MARKER.test(source)) {
    throw new PrepareError("Only Pine Script is accepted: //@PineTS marks PineTS's JavaScript syntax", file)
  }
  const version = VERSION_LINE.exec(source)
  if (!version) throw new PrepareError('The script must say its Pine version, //@version=5 or //@version=6', file, 1, 1)
  const number = Number(version[1])
  if (!SUPPORTED_VERSIONS.includes(number)) {
    throw new PrepareError(`Pine Script version ${number} is not supported: versions 5 and 6 are`, file)
  }
  return number
}

/** The index in `tokens` of the bracket closing the one at `open`. */
function closing(tokens: Token[], open: number): number {
  const pairs: Record<string, string> = { '(': ')', '[': ']' }
  const stack: string[] = []
  for (let index = open; index < tokens.length; index += 1) {
    const text = tokens[index].text
    if (tokens[index].kind !== 'operator') continue
    if (text in pairs) stack.push(pairs[text])
    else if (text === stack[stack.length - 1]) {
      stack.pop()
      if (stack.length === 0) return index
    }
  }
  return -1
}

/** Top-level statements: a line starting at column 1, and every indented or empty line after it. */
function statements(lines: Token[][]): Statement[] {
  const found: Statement[] = []
  lines.forEach((line, index) => {
    const head = line[0]
    const top = head !== undefined && head.kind !== 'space' && head.kind !== 'comment'
    if (top) found.push({ first: index, last: index })
    else if (found.length > 0) found[found.length - 1].last = index
  })
  return found
}

function statementTokens(lines: Token[][], statement: Statement): Token[] {
  return significant(lines.slice(statement.first, statement.last + 1).flat())
}

interface Declaration {
  kind: ScriptKind
  title: string | null
  statement: Statement
  /** Index in the lines of the line holding the declaration's closing parenthesis. */
  endLine: number
}

function declarationOf(lines: Token[][], file: string): Declaration {
  const all = statements(lines)
  const first = all[0]
  const tokens = first ? statementTokens(lines, first) : []
  const head = tokens[0]
  if (!head || !DECLARATIONS.includes(head.text) || tokens[1]?.text !== '(') {
    throw new PrepareError('The script must start with indicator(), strategy() or library()', file, head?.line ?? 1, 1)
  }
  const end = closing(tokens, 1)
  if (end === -1) throw new PrepareError(`The ${head.text}() declaration is not closed`, file, head.line, head.column)
  let title: string | null = null
  const titleToken = tokens.slice(2, end).find((token) => token.kind === 'string')
  if (titleToken) title = titleToken.text.slice(1, -1)
  return { kind: head.text as ScriptKind, title, statement: first, endLine: tokens[end].line - 1 }
}

interface Import {
  name: string
  alias: string
  line: number
}

function importOf(tokens: Token[], file: string): Import {
  // import user/Name/1 [as alias]
  const [, user, slash1, name, slash2, version, as, alias] = tokens
  const valid =
    user?.kind === 'identifier' && slash1?.text === '/' && name?.kind === 'identifier' &&
    slash2?.text === '/' && version?.kind === 'number' &&
    (tokens.length === 6 || (tokens.length === 8 && as?.text === 'as' && alias?.kind === 'identifier'))
  if (!valid) throw new PrepareError('An import reads: import user/Library/1 as alias', file, tokens[0].line, 1)
  return { name: name.text, alias: alias?.text ?? name.text, line: tokens[0].line }
}

/** One line of output, made from the tokens of a source line with some replaced. */
function render(line: Token[], replaced: Map<Token, string>): string {
  return line.map((token) => replaced.get(token) ?? token.text).join('')
}

interface LibraryInfo {
  source: LibrarySource
  /** Every name declared at the library's top level, renamed `Name__name`. */
  names: Set<string>
  exports: Set<string>
  lines: string[]
  origins: LineOrigin[]
  warnings: string[]
}

const prefixed = (library: string, name: string) => `${library}__${name}`

/**
 * Replace `alias.name` by the inlined name, for the aliases of `imports`.
 * A name the library does not export is refused where it is written.
 */
function replaceImported(
  tokens: Token[],
  imports: Map<string, LibraryInfo>,
  replaced: Map<Token, string>,
  file: string,
) {
  for (let index = 0; index + 2 < tokens.length; index += 1) {
    const [alias, dot, name] = [tokens[index], tokens[index + 1], tokens[index + 2]]
    if (alias.kind !== 'identifier' || dot.text !== '.' || name.kind !== 'identifier') continue
    if (index > 0 && tokens[index - 1].text === '.') continue
    const library = imports.get(alias.text)
    if (!library) continue
    if (!library.exports.has(name.text)) {
      throw new PrepareError(
        `${alias.text}.${name.text}: the library ${library.source.name} exports no "${name.text}"`,
        file, name.line, name.column,
      )
    }
    replaced.set(alias, prefixed(library.source.name, name.text))
    replaced.set(dot, '')
    replaced.set(name, '')
  }
}

/** The names a statement declares at the top level, and whether they are exported. */
function topLevelNames(tokens: Token[], file: string): { names: string[]; exported: boolean; kind: string } {
  let index = 0
  const exported = tokens[0]?.text === 'export'
  if (exported) index += 1
  const head = tokens[index]
  if (!head) return { names: [], exported, kind: 'empty' }
  if (head.text === 'method') {
    throw new PrepareError('Methods in a library are not supported yet: write them as functions', file, head.line, head.column)
  }
  if ((head.text === 'type' || head.text === 'enum') && tokens[index + 1]?.kind === 'identifier') {
    return { names: [tokens[index + 1].text], exported, kind: head.text }
  }
  if (head.kind === 'identifier' && tokens[index + 1]?.text === '(') {
    const end = closing(tokens, index + 1)
    if (end !== -1 && tokens[end + 1]?.text === '=>') return { names: [head.text], exported, kind: 'function' }
  }
  if (head.text === '[') {
    const end = closing(tokens, index)
    if (end !== -1 && tokens[end + 1]?.text === '=') {
      const names = tokens.slice(index + 1, end).filter((token) => token.kind === 'identifier').map((token) => token.text)
      return { names, exported, kind: 'variable' }
    }
  }
  // [var|varip|const] [type] name = …
  let cursor = index
  while (
    tokens[cursor] &&
    tokens[cursor + 1]?.kind === 'identifier' &&
    (DECLARATION_WORDS.has(tokens[cursor].text) || tokens[cursor].kind === 'identifier')
  ) {
    cursor += 1
  }
  if (tokens[cursor]?.kind === 'identifier' && tokens[cursor + 1]?.text === '=') {
    return { names: [tokens[cursor].text], exported, kind: 'variable' }
  }
  return { names: [], exported, kind: 'statement' }
}

/** Parameters and local variables of a function, to refuse those named like a top-level name. */
function localNames(tokens: Token[]): Token[] {
  const found: Token[] = []
  // The parameters: at depth 1 of the first parentheses, a name before "," ")" or "=".
  const open = tokens.findIndex((token) => token.text === '(')
  const end = open === -1 ? -1 : closing(tokens, open)
  if (end !== -1 && tokens[end + 1]?.text === '=>') {
    let depth = 0
    for (let index = open; index <= end; index += 1) {
      const token = tokens[index]
      if (token.text === '(' || token.text === '[') depth += 1
      else if (token.text === ')' || token.text === ']') depth -= 1
      else if (depth === 1 && token.kind === 'identifier' && [',', ')', '='].includes(tokens[index + 1]?.text ?? '')) {
        found.push(token)
      }
    }
  }
  // Local declarations: a name followed by "=" where a statement starts, loop
  // variables, and the names of a tuple declaration ([a, b] = …).
  for (let index = end + 1; index < tokens.length; index += 1) {
    const token = tokens[index]
    const next = tokens[index + 1]?.text
    const previous = tokens[index - 1]?.text
    if (token.text === '[') {
      const close = closing(tokens, index)
      if (close !== -1 && (tokens[close + 1]?.text === '=' || tokens[close + 1]?.text === 'in')) {
        found.push(...tokens.slice(index + 1, close).filter((inner) => inner.kind === 'identifier'))
      }
      continue
    }
    if (token.kind !== 'identifier') continue
    if (previous === 'for' || (next === '=' && previous !== '.' && previous !== ',' && previous !== '(')) {
      found.push(token)
    }
  }
  return found
}

function inlineLibrary(
  source: LibrarySource,
  imports: Map<string, LibraryInfo>,
): LibraryInfo {
  const file = source.name
  const version = checkedVersion(source.source, file)
  const tokens = tokenize(source.source)
  const lines = linesOf(tokens)
  const declaration = declarationOf(lines, file)
  if (declaration.kind !== 'library') {
    throw new PrepareError(`"${source.name}" is not a library: it declares ${declaration.kind}()`, file, 1, 1)
  }
  const warnings: string[] = []
  const kept: Statement[] = []
  const names = new Set<string>()
  const exports = new Set<string>()
  const locals: Token[] = []
  // The fields of a type or the members of an enum are not top-level names: only
  // the name in the declaration's first line is renamed.
  const bodies = new Set<Token>()
  for (const statement of statements(lines)) {
    if (statement.first === declaration.statement.first) continue
    const own = statementTokens(lines, statement)
    if (own[0]?.text === 'import') continue
    const declared = topLevelNames(own, file)
    if (declared.kind === 'statement') {
      warnings.push(`${file}, line ${statement.first + 1}: left out (only declarations are imported from a library)`)
      continue
    }
    kept.push(statement)
    declared.names.forEach((name) => names.add(name))
    if (declared.exported) declared.names.forEach((name) => exports.add(name))
    if (declared.kind === 'function') locals.push(...localNames(own))
    if (declared.kind === 'type' || declared.kind === 'enum') {
      lines.slice(statement.first + 1, statement.last + 1).flat().forEach((token) => bodies.add(token))
    }
  }
  const shadowing = locals.find((token) => names.has(token.text))
  if (shadowing) {
    throw new PrepareError(
      `"${shadowing.text}" names both something declared at the top of the library and a parameter or local ` +
        'variable: rename one of them so that it can be imported',
      file, shadowing.line, shadowing.column,
    )
  }

  const replaced = new Map<Token, string>()
  const keptTokens = kept.flatMap((statement) => lines.slice(statement.first, statement.last + 1).flat())
  const meaningful = significant(keptTokens)
  replaceImported(meaningful, imports, replaced, file)
  let depth = 0
  meaningful.forEach((token, index) => {
    if (token.text === '(' || token.text === '[') depth += 1
    if (token.text === ')' || token.text === ']') depth -= 1
    if (token.kind !== 'identifier' || !names.has(token.text) || replaced.has(token) || bodies.has(token)) return
    const previous = meaningful[index - 1]
    const next = meaningful[index + 1]
    if (previous?.text === '.') return
    // A named argument names a parameter, not the library's declaration.
    if (depth > 0 && next?.text === '=') return
    replaced.set(token, prefixed(file, token.text))
  })
  for (const statement of kept) {
    const head = lines[statement.first].find((token) => token.kind !== 'space')
    if (head?.text === 'export') {
      replaced.set(head, '')
      const after = lines[statement.first][lines[statement.first].indexOf(head) + 1]
      if (after?.kind === 'space') replaced.set(after, '')
    }
  }
  for (const token of keptTokens) {
    if (token.kind === 'comment' && VERSION_LINE.test(token.text)) replaced.set(token, `// (Pine v${version} library)`)
  }

  const out: string[] = []
  const origins: LineOrigin[] = []
  for (const statement of kept) {
    for (let index = statement.first; index <= statement.last; index += 1) {
      out.push(render(lines[index], replaced))
      origins.push({ file, line: index + 1 })
    }
  }
  return { source, names, exports, lines: out, origins, warnings }
}

/**
 * The script ready for PineTS: native Pine checked, its libraries inlined.
 * Throws PrepareError with the file and line to show.
 */
export async function prepareScript(source: string, resolve: LibraryResolver): Promise<PreparedScript> {
  const version = checkedVersion(source)
  const tokens = tokenize(source)
  const lines = linesOf(tokens)
  const declaration = declarationOf(lines, SCRIPT_FILE)

  const done = new Map<string, LibraryInfo>()
  const order: LibraryInfo[] = []
  const warnings: string[] = []

  const importsOf = (sourceLines: Token[][], file: string): Import[] =>
    statements(sourceLines)
      .map((statement) => statementTokens(sourceLines, statement))
      .filter((own) => own[0]?.text === 'import')
      .map((own) => importOf(own, file))

  const load = async (name: string, chain: string[], file: string, line: number): Promise<LibraryInfo> => {
    const known = done.get(name)
    if (known) return known
    if (chain.includes(name)) {
      throw new PrepareError(`The libraries import each other: ${[...chain, name].join(' → ')}`, file, line, 1)
    }
    const library = await resolve(name)
    if (!library) {
      throw new PrepareError(`No library "${name}" in WATA: create it on the Indicators page`, file, line, 1)
    }
    const libraryLines = linesOf(tokenize(library.source))
    const aliases = new Map<string, LibraryInfo>()
    for (const dependency of importsOf(libraryLines, name)) {
      aliases.set(dependency.alias, await load(dependency.name, [...chain, name], name, dependency.line))
    }
    const info = inlineLibrary(library, aliases)
    if (checkedVersion(library.source, name) !== version) {
      info.warnings.push(
        `${name} is Pine v${checkedVersion(library.source, name)} and runs here as v${version}, the script's version`,
      )
    }
    done.set(name, info)
    order.push(info)
    return info
  }

  const aliases = new Map<string, LibraryInfo>()
  const imports = importsOf(lines, SCRIPT_FILE)
  for (const entry of imports) {
    aliases.set(entry.alias, await load(entry.name, [], SCRIPT_FILE, entry.line))
  }
  if (declaration.kind === 'library' && imports.length > 0) {
    warnings.push('A library is checked here with the libraries it imports inlined')
  }

  const replaced = new Map<Token, string>()
  replaceImported(significant(tokens), aliases, replaced, SCRIPT_FILE)
  const importLines = new Set(imports.map((entry) => entry.line - 1))

  const out: string[] = []
  const lineMap: LineOrigin[] = []
  const pushScriptLine = (index: number) => {
    out.push(importLines.has(index) ? '' : render(lines[index], replaced))
    lineMap.push({ file: SCRIPT_FILE, line: index + 1 })
  }
  for (let index = 0; index <= declaration.endLine; index += 1) pushScriptLine(index)
  for (const library of order) {
    out.push(`// ── library ${library.source.name}, version ${library.source.version} (inlined by WATA) ──`)
    lineMap.push({ file: library.source.name, line: 0 })
    out.push(...library.lines)
    lineMap.push(...library.origins)
    warnings.push(...library.warnings)
  }
  for (let index = declaration.endLine + 1; index < lines.length; index += 1) pushScriptLine(index)

  return {
    source: out.join('\n'),
    pineVersion: version,
    kind: declaration.kind,
    title: declaration.title,
    libraries: order.map((library) => ({
      id: library.source.id,
      name: library.source.name,
      version: library.source.version,
    })),
    lineMap,
    warnings,
  }
}

/**
 * A library's own code, checked as PineTS can: PineTS has no library(), so the
 * library is inlined into an empty indicator of its version.
 */
export async function prepareLibraryCheck(
  source: string,
  name: string,
  resolve: LibraryResolver,
): Promise<PreparedScript> {
  const version = checkedVersion(source)
  const shell = `//@version=${version}\nindicator("${name.replaceAll('"', '')} (library check)")\nimport wata/${name}/1 as checked\n`
  const prepared = await prepareScript(shell, async (wanted) =>
    wanted === name ? { id: 'checked', name, version: 0, source } : resolve(wanted),
  )
  return {
    ...prepared,
    kind: 'library',
    lineMap: prepared.lineMap.map((origin) => (origin.file === name ? { file: SCRIPT_FILE, line: origin.line } : origin)),
  }
}

/** Where a line of the prepared source comes from (1-based line), for diagnostics. */
export function originOf(prepared: PreparedScript, line: number): LineOrigin | null {
  return prepared.lineMap[line - 1] ?? null
}
