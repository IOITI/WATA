// Whether a Pine script can become a signal rule as it is, read from its source
// without running it: the rule check's first part. Its second, the run test on
// recorded bars, is src/research/rule_check.py's.
//
// - The alerts a rule maps (alertcondition() titles; alert() calls, taken as
//   messages or not at all), and the events the script only draws (plotshape,
//   plotchar, a colour given on a condition, an alert() or an order under an if):
//   alerts it could have, which withAlerts writes in.
// - What makes its signals differ live, where each new bar is the last of its run,
//   and in a backtest, where one run takes a whole engine day: what a run gives
//   otherwise at its last bar (the engine's wholeBecause, wholeRuns.ts, held to the
//   same words by ruleCheck.test.ts), the clock, chance, other series. Each is
//   followed through the script to what decides its alerts: one that reaches none,
//   a dashboard drawn at the last bar, is only noted. What cannot be followed is
//   taken to reach them.
// - What WATA serves otherwise than TradingView: syminfo, the days' time zone,
//   volume.
//
// Each finding says where (its line, a library's own line), what it means and what
// to change. The editor marks them (editor/diagnostics.ts); the server's checker
// (checker.ts) gives them to the rule check of a script or of a LuxAlgo source.
// Pure: nothing here runs a script.

import { linesOf, significant, tokenize } from './lexer'
import type { Token } from './lexer'
import { callArguments, messageAction } from './outline'
import { checkedVersion, originOf, PrepareError, SCRIPT_FILE } from './prepare'
import type { PreparedScript, ScriptKind } from './prepare'
import { parseTicker } from './tickers'
import { wholeBecause } from './wholeRuns'

/** Raised when what the check finds changes: every report made before is made again (it is in their digest). */
export const RULE_CHECK_VERSION = 1

export type FindingArea = 'repaint' | 'alerts' | 'wata'
/** Worst first: not a rule as it is, repaints, may repaint (the run test decides), to change, worth knowing. */
export type FindingLevel = 'blocks' | 'repaints' | 'risk' | 'change' | 'note'
export const LEVELS: readonly FindingLevel[] = ['blocks', 'repaints', 'risk', 'change', 'note']

/** What the check says of a script as a whole, worst first. */
export type RuleVerdict = 'blocked' | 'needs_alerts' | 'repaints' | 'may_repaint' | 'to_change' | 'ready'

export interface Place {
  /** null for the script itself, else the name of the library the line is in. */
  file: string | null
  line: number
  column: number
}

export interface RuleFinding {
  id: string
  area: FindingArea
  level: FindingLevel
  message: string
  fix: string | null
  /** Its first places, MAX_PLACES at most: ``count`` says how many there are. */
  places: Place[]
  count: number
  /** The code at its first place. */
  excerpt: string | null
  /** The script's names it goes through to reach the alerts, when it reaches them. */
  through: string[] | null
}

export interface DeclaredAlert {
  /** null when the title is not written as text: a rule cannot map it. */
  title: string | null
  message: string | null
  line: number
}

export interface AlertCall {
  message: string | null
  line: number
}

export interface AlertCandidate {
  title: string
  condition: string
  /** What it was read from: plotshape, plotchar, bgcolor, alert, strategy.entry... */
  call: string
  line: number
  /** Where withAlerts writes its alertcondition: before or after that line. */
  at: { line: number; where: 'before' | 'after' }
  /** What in it may make it differ live and in a backtest, by finding id. */
  risks: string[]
}

export interface RuleCheck {
  version: number
  kind: ScriptKind | null
  pineVersion: number | null
  alerts: DeclaredAlert[]
  alertCalls: AlertCall[]
  candidates: AlertCandidate[]
  plots: string[]
  wholeBecause: string | null
  findings: RuleFinding[]
  verdict: RuleVerdict
}

const MAX_PLACES = 20
const EXCERPT_CHARS = 160
const MAX_CANDIDATES = 20
const MAX_THROUGH = 6
const MAX_PASSES = 40

// ── reading the code ──

type SourceKind =
  | 'bar-state'
  | 'isconfirmed'
  | 'bar-end'
  | 'clock'
  | 'random'
  | 'visible-range'
  | 'other-series'
  | 'lookahead'
  | 'no-data'
  | 'strategy-state'
  | 'mintick'
  | 'syminfo'
  | 'volume'
  | 'utc-time'

interface Source {
  kind: SourceKind
  name: string
}

const NO_DATA = new Set(['financial', 'economic', 'earnings', 'dividends', 'splits', 'quandl', 'seed', 'currency_rate', 'footprint'])
// What PineTS's syminfo holds on WATA's assets: the rest is undefined there.
const SYMINFO_GIVEN = new Set(['ticker', 'tickerid', 'description'])
const VOLUME_TA = new Set(['vwap', 'vwma', 'obv', 'pvt', 'accdist', 'nvi', 'pvi', 'wvad', 'iii', 'mfi'])
const STRATEGY_CONSTANTS = new Set(['long', 'short', 'fixed', 'cash', 'percent_of_equity', 'direction', 'commission', 'oca'])
// The parts of a time that a day's time zone changes: read without one, they are UTC's on WATA.
const TIME_PARTS = new Set(['hour', 'minute', 'second', 'dayofweek', 'dayofmonth'])
// Read bare, the parts of the bar's own time: those worth a word.
const BARE_TIME_PARTS = new Set(['hour', 'minute', 'dayofweek', 'dayofmonth'])
// Pine's namespaces whose calls change their first argument, and the names of the calls that do.
const MUTATED = new Set(['array', 'map', 'matrix', 'line', 'label', 'box', 'table', 'linefill', 'polyline'])
const MUTATOR = /^(?:set\w*|push|unshift|insert|put(?:_all)?|fill|remove\w*|pop|shift|clear|sort\w*|reverse|delete|concat|add_\w+|swap_\w+|merge_cells|cell\w*)$/
const ASSIGNMENTS = new Set(['=', ':=', '+=', '-=', '*=', '/=', '%='])
const BLOCK_WORDS = new Set(['if', 'switch', 'for', 'while'])
// Calls that draw a series, and the position of their title.
const PLOT_TITLES: Record<string, number> = { plot: 1, plotshape: 1, plotchar: 1, plotarrow: 1, plotcandle: 4, plotbar: 4 }

function unquoted(token: Token): string {
  const body = token.text.slice(1, token.text.length > 1 && token.text.endsWith(token.text[0]) ? -1 : undefined)
  return body.replace(/\\(.)/g, (_, char: string) => (char === 'n' ? '\n' : char === 't' ? '\t' : char))
}

function isNamed(argument: Token[], name?: string): boolean {
  return argument.length >= 2 && argument[0].kind === 'identifier' && argument[1].text === '=' && (name === undefined || argument[0].text === name)
}

/** The argument named ``name``, or the one at ``position`` among those not named. */
function argument(args: Token[][], name: string, position: number | null): Token[] | null {
  const named = args.find((entry) => isNamed(entry, name))
  if (named) return named.slice(2)
  return position === null ? null : (args.filter((entry) => !isNamed(entry))[position] ?? null)
}

function literal(tokens: Token[] | null): string | null {
  return tokens && tokens.length === 1 && tokens[0].kind === 'string' ? unquoted(tokens[0]) : null
}

/** An expression's tokens written back as Pine, on one line. */
function codeText(tokens: Token[]): string {
  let text = ''
  tokens.forEach((token, index) => {
    const before = tokens[index - 1]
    const tight =
      index === 0 ||
      before.text === '.' ||
      before.text === '(' ||
      before.text === '[' ||
      token.text === '.' ||
      token.text === ')' ||
      token.text === ']' ||
      token.text === ',' ||
      ((token.text === '(' || token.text === '[') && (before.kind === 'identifier' || before.text === ')' || before.text === ']'))
    text += (tight ? '' : ' ') + token.text
  })
  return text
}

/** The index of the bracket closing the one at ``open``, or the last token. */
function closing(tokens: Token[], open: number): number {
  let depth = 0
  for (let index = open; index < tokens.length; index += 1) {
    const text = tokens[index].text
    if (tokens[index].kind !== 'operator') continue
    if (text === '(' || text === '[') depth += 1
    else if (text === ')' || text === ']') {
      depth -= 1
      if (depth === 0) return index
    }
  }
  return tokens.length - 1
}

/** The index of the first operator ``wanted`` outside every bracket, or -1. */
function atTop(tokens: Token[], wanted: Set<string>, from = 0): number {
  let depth = 0
  for (let index = from; index < tokens.length; index += 1) {
    const token = tokens[index]
    if (token.kind !== 'operator') continue
    if (token.text === '(' || token.text === '[') depth += 1
    else if (token.text === ')' || token.text === ']') depth -= 1
    else if (depth === 0 && wanted.has(token.text)) return index
  }
  return -1
}

/** ``cond ? value : na``: the condition, when what is drawn otherwise is nothing. */
function ternaryCondition(tokens: Token[]): Token[] | null {
  const question = atTop(tokens, new Set(['?']))
  if (question <= 0) return null
  const colon = atTop(tokens, new Set([':']), question + 1)
  if (colon < 0) return null
  const otherwise = codeText(tokens.slice(colon + 1))
  return otherwise === 'na' || otherwise === 'color(na)' ? tokens.slice(0, question) : null
}

function hasOffset(tokens: Token[]): boolean {
  return tokens.some((token, index) =>
    token.text === '[' && tokens[index + 1]?.kind === 'number' && Number(tokens[index + 1].text) >= 1 && tokens[index + 2]?.text === ']')
}

// ── statements and blocks ──

interface Statement {
  /** Its tokens that carry meaning, over all its lines. */
  tokens: Token[]
  level: number
  /** Its first and last line, 1-based, in the text read. */
  line: number
  lastLine: number
  /** The statement whose block holds it, -1 at the top. */
  parent: number
}

function widthOf(space: string): number {
  let width = 0
  for (const char of space) width += char === '\t' ? 4 : char === ' ' ? 1 : 0
  return width
}

/** Its statements: a line that is not indented by a multiple of four, or inside brackets, goes on the one before. */
function statementsOf(tokens: Token[]): Statement[] {
  const found: Statement[] = []
  let depth = 0
  const lines = linesOf(tokens)
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index]
    const code = line.filter((token) => token.kind !== 'space' && token.kind !== 'comment')
    if (code.length === 0) continue
    const lead = line[0].kind === 'space' ? widthOf(line[0].text) : 0
    const current = found[found.length - 1]
    if (current && (depth > 0 || lead % 4 !== 0)) {
      current.tokens.push(...code)
      current.lastLine = index + 1
    } else {
      found.push({ tokens: [...code], level: Math.floor(lead / 4), line: index + 1, lastLine: index + 1, parent: -1 })
    }
    for (const token of code) {
      if (token.kind !== 'operator') continue
      if (token.text === '(' || token.text === '[') depth += 1
      else if ((token.text === ')' || token.text === ']') && depth > 0) depth -= 1
    }
  }
  const open: number[] = []
  found.forEach((statement, index) => {
    while (open.length > 0 && found[open[open.length - 1]].level >= statement.level) open.pop()
    statement.parent = open.length > 0 ? open[open.length - 1] : -1
    open.push(index)
  })
  return found
}

interface Definition {
  name: string
  params: Set<string>
  /** Where its body starts in its statement's tokens: after =>, which ends it when it is a block. */
  body: number
}

function definitionOf(tokens: Token[]): Definition | null {
  let index = 0
  if (tokens[index]?.text === 'export') index += 1
  if (tokens[index]?.text === 'method') index += 1
  const name = tokens[index]
  if (name?.kind !== 'identifier' || tokens[index + 1]?.text !== '(') return null
  const end = closing(tokens, index + 1)
  if (tokens[end + 1]?.text !== '=>') return null
  const params = new Set(tokens.slice(index + 2, end).filter((token) => token.kind === 'identifier').map((token) => token.text))
  return { name: name.text, params, body: end + 2 }
}

interface Assignment {
  targets: string[]
  /** Where its value starts. */
  value: number
}

function assignmentOf(tokens: Token[]): Assignment | null {
  const first = tokens[0]?.text
  if (first === undefined || ['if', 'else', 'for', 'while', 'switch', 'import', 'type', 'export', 'method'].includes(first)) return null
  const at = atTop(tokens, ASSIGNMENTS)
  if (at <= 0) return null
  if (tokens[0].text === '[') {
    const end = closing(tokens, 0)
    return { targets: tokens.slice(1, end).filter((token) => token.kind === 'identifier').map((token) => token.text), value: at + 1 }
  }
  // The name before the operator, or the first of a field's names (``state.count := 1``).
  let target = at - 1
  while (target >= 2 && tokens[target - 1].text === '.' && tokens[target - 2].kind === 'identifier') target -= 2
  return tokens[target].kind === 'identifier' ? { targets: [tokens[target].text], value: at + 1 } : null
}

/** The script's names a statement changes by calling something on them: ``a.push(x)``, ``array.set(a, 0, x)``. */
function mutatedBy(tokens: Token[], methods: Set<string>): string[] {
  const changed: string[] = []
  for (let index = 0; index + 2 < tokens.length; index += 1) {
    if (tokens[index].kind !== 'identifier' || tokens[index - 1]?.text === '.') continue
    // The chain a.b.c(...) from its first name.
    let end = index
    while (tokens[end + 1]?.text === '.' && tokens[end + 2]?.kind === 'identifier') end += 2
    if (end === index || tokens[end + 1]?.text !== '(') continue
    const method = tokens[end].text
    if (MUTATED.has(tokens[index].text) && end === index + 2) {
      if (!MUTATOR.test(method)) continue
      const first = callArguments(tokens, end + 1).args[0]
      if (first?.[0]?.kind === 'identifier') changed.push(first[0].text)
    } else if (MUTATOR.test(method) || methods.has(method)) {
      changed.push(tokens[index].text)
    }
  }
  return changed
}

// ── following what may repaint ──

interface Path {
  name: string
  place: Place
  through: string[]
}

type Taint = Map<SourceKind, Path>

function merge(into: Taint, from: Taint | undefined, via: string | null = null): boolean {
  if (!from) return false
  let changed = false
  for (const [kind, path] of from) {
    if (into.has(kind)) continue
    const through = via !== null && path.through.length < MAX_THROUGH && path.through[path.through.length - 1] !== via
      ? [...path.through, via]
      : path.through
    into.set(kind, { ...path, through })
    changed = true
  }
  return changed
}

interface Reading {
  text: string
  lines: string[]
  tokens: Token[]
  statements: Statement[]
  sources: Map<Token, Source>
  place: (token: Token) => Place
}

/** The constructs of a source: where a run gives otherwise at its last bar, the clock, other series... */
function sourcesOf(tokens: Token[]): Map<Token, Source> {
  const found = new Map<Token, Source>()
  for (let index = 0; index < tokens.length; index += 1) {
    const token = tokens[index]
    if (token.kind !== 'identifier' || tokens[index - 1]?.text === '.') continue
    const next = tokens[index + 1]?.text
    const field = next === '.' && tokens[index + 2]?.kind === 'identifier' ? tokens[index + 2].text : null
    const called = (at: number) => tokens[at]?.text === '('
    const named = (kind: SourceKind, name: string) => found.set(token, { kind, name })
    if (field !== null) {
      const name = `${token.text}.${field}`
      if (token.text === 'barstate' && field !== 'isfirst') named(field === 'isconfirmed' || field === 'isnew' ? 'isconfirmed' : 'bar-state', name)
      else if (token.text === 'session' && (field === 'islastbar' || field === 'islastbar_regular')) named('bar-end', name)
      else if (token.text === 'math' && field === 'random') named('random', name)
      else if (token.text === 'chart' && (field === 'left_visible_bar_time' || field === 'right_visible_bar_time')) named('visible-range', name)
      else if (token.text === 'request') {
        if (NO_DATA.has(field)) named('no-data', name)
        else if ((field === 'security' || field === 'security_lower_tf') && called(index + 3)) {
          const args = callArguments(tokens, index + 3).args
          const lookahead = argument(args, 'lookahead', 4)
          const ahead = lookahead !== null && /lookahead_on|^true$/.test(codeText(lookahead))
          named(ahead && !hasOffset(argument(args, 'expression', 2) ?? []) ? 'lookahead' : 'other-series', name)
        } else named('other-series', name)
      } else if (token.text === 'strategy' && !called(index + 3) && tokens[index + 3]?.text !== '.' && !STRATEGY_CONSTANTS.has(field)) {
        named('strategy-state', name)
      } else if (token.text === 'syminfo' && !SYMINFO_GIVEN.has(field)) named(field === 'mintick' ? 'mintick' : 'syminfo', name)
      else if (token.text === 'ta' && VOLUME_TA.has(field)) named('volume', name)
      continue
    }
    if (token.text === 'last_bar_index' || token.text === 'last_bar_time') named('bar-end', token.text)
    else if (token.text === 'timenow') named('clock', token.text)
    else if (token.text === 'volume' && !called(index + 1)) named('volume', token.text)
    else if (TIME_PARTS.has(token.text)) {
      if (called(index + 1)) {
        const args = callArguments(tokens, index + 1).args
        if (!args.some((entry) => isNamed(entry, 'timezone')) && args.filter((entry) => !isNamed(entry)).length < 2) named('utc-time', `${token.text}()`)
      } else if (BARE_TIME_PARTS.has(token.text)) named('utc-time', token.text)
    } else if ((token.text === 'time' || token.text === 'time_close') && called(index + 1)) {
      const args = callArguments(tokens, index + 1).args
      const plain = args.filter((entry) => !isNamed(entry))
      const session = args.some((entry) => isNamed(entry, 'session')) || plain.length >= 2
      if (session && !args.some((entry) => isNamed(entry, 'timezone')) && plain.length < 3) named('utc-time', `${token.text}()`)
    } else if (token.text === 'timestamp' && called(index + 1)) {
      const first = callArguments(tokens, index + 1).args[0]
      const date = first?.length === 1 && first[0].kind === 'string' ? unquoted(first[0]) : null
      const zoneless = date === null ? first !== undefined : /^\d{4}-\d{2}-\d{2}/.test(date) && !/(?:[+-]\d{2}:?\d{2}|Z|GMT|UTC)\s*$/.test(date)
      if (zoneless) named('utc-time', 'timestamp()')
    }
  }
  return found
}

interface Trace {
  /** What each sink of the alerts reads, joined: what reaches them. */
  reached: Taint
  /** What the statement starting at a line of the script reads (a candidate's), by that line. */
  atLine: (line: number) => Taint
}

/** What each name, function and alert may read, followed to a fixed point. */
function traceOf(reading: Reading, scriptLine: (line: number) => number | null): Trace {
  const { statements, sources } = reading
  const names = new Map<string, Taint>()
  const functions = new Map<string, Taint>()
  const calledWith = new Map<string, Taint>()
  const writes = new Map<string, Set<string>>()
  const definitions = new Map<number, Definition>()
  const owner: number[] = statements.map(() => -1)
  const switches = new Set<number>()
  statements.forEach((statement, index) => {
    const parent = statement.parent
    const caseLine = parent !== -1 && switches.has(parent)
    const definition = caseLine ? null : definitionOf(statement.tokens)
    if (definition) {
      definitions.set(index, definition)
      functions.set(definition.name, new Map())
      writes.set(definition.name, new Set())
    }
    const head = statement.tokens[0]?.text
    const assigned = assignmentOf(statement.tokens)
    if (head === 'switch' || (assigned && statement.tokens[assigned.value]?.text === 'switch')) switches.add(index)
    owner[index] = parent === -1 ? -1 : definitions.has(parent) ? parent : owner[parent]
  })
  const methods = new Set([...definitions.values()].map((definition) => definition.name))

  const taintOf = (tokens: Token[], from = 0, to = tokens.length): Taint => {
    const taint: Taint = new Map()
    for (let index = from; index < to; index += 1) {
      const token = tokens[index]
      if (token.kind !== 'identifier') continue
      const source = sources.get(token)
      if (source) {
        if (!taint.has(source.kind)) taint.set(source.kind, { name: source.name, place: reading.place(token), through: [] })
        continue
      }
      const call = tokens[index + 1]?.text === '('
      if (call && functions.has(token.text)) merge(taint, functions.get(token.text), token.text)
      else if (tokens[index - 1]?.text !== '.') merge(taint, names.get(token.text), token.text)
    }
    return taint
  }

  // What a block's header decides on: its condition, and those of the ifs before an else.
  const conditionOf = (index: number): Taint => {
    const statement = statements[index]
    const tokens = statement.tokens
    const head = tokens[0]?.text
    if (definitions.has(index)) return new Map()
    const parent = statement.parent
    if (parent !== -1 && switches.has(parent)) {
      const arrow = atTop(tokens, new Set(['=>']))
      return taintOf(tokens, 0, arrow < 0 ? tokens.length : arrow)
    }
    if (head === 'else') {
      const taint = taintOf(tokens, 1)
      for (let before = index - 1; before > parent; before -= 1) {
        const sibling = statements[before]
        if (sibling.level !== statement.level) continue
        merge(taint, taintOf(sibling.tokens))
        if (sibling.tokens[0]?.text !== 'else') break
      }
      return taint
    }
    if (head !== undefined && BLOCK_WORDS.has(head)) return taintOf(tokens, 1)
    const assigned = assignmentOf(tokens)
    if (assigned && BLOCK_WORDS.has(tokens[assigned.value]?.text ?? '')) return taintOf(tokens, assigned.value)
    return new Map()
  }

  // The conditions of the blocks around a statement, within its function when it is in one.
  const blocksOf = (index: number): Taint => {
    const taint: Taint = new Map()
    for (let parent = statements[index].parent; parent !== -1; parent = statements[parent].parent) merge(taint, conditionOf(parent))
    return taint
  }
  // And what decides whether its function is called at all: what it changes, or alerts, depends on that too.
  const controlOf = (index: number): Taint => {
    const taint = blocksOf(index)
    const within = owner[index]
    if (within !== -1) merge(taint, calledWith.get(definitions.get(within)?.name ?? ''))
    return taint
  }

  const spread = (target: string, taint: Taint, within: number): boolean => {
    let known = names.get(target)
    if (!known) {
      known = new Map()
      names.set(target, known)
    }
    const changed = merge(known, taint)
    if (within !== -1) writes.get(definitions.get(within)?.name ?? '')?.add(target)
    return changed
  }

  for (let pass = 0; pass < MAX_PASSES; pass += 1) {
    let changed = false
    statements.forEach((statement, index) => {
      const tokens = statement.tokens
      const within = owner[index]
      const control = controlOf(index)
      const definition = definitions.get(index)
      if (definition) {
        // A function reads what its body reads: a one-line one after =>, a block's in the statements under it.
        const own = functions.get(definition.name)
        if (own && definition.body < tokens.length) changed = merge(own, taintOf(tokens, definition.body)) || changed
        return
      }
      if (within !== -1) {
        // What a function gives reads what its body reads, under its own blocks: not what its callers decide on.
        const own = functions.get(definitions.get(within)?.name ?? '')
        if (own) changed = merge(own, taintOf(tokens)) || changed
        if (own) changed = merge(own, blocksOf(index)) || changed
      }
      const own: Taint = new Map(control)
      if (statement.parent !== -1 && switches.has(statement.parent)) merge(own, conditionOf(index))
      const assigned = assignmentOf(tokens)
      if (assigned) {
        const value = new Map(own)
        merge(value, taintOf(tokens, assigned.value))
        // ``x = if cond`` holds what its block ends with: every name read under it.
        if (BLOCK_WORDS.has(tokens[assigned.value]?.text ?? '')) {
          for (let child = index + 1; child < statements.length && statements[child].level > statement.level; child += 1) {
            merge(value, taintOf(statements[child].tokens))
          }
        }
        for (const target of assigned.targets) changed = spread(target, value, within) || changed
      } else if (tokens[0]?.text === 'for') {
        const value = new Map(own)
        merge(value, taintOf(tokens, 1))
        const loop = tokens.slice(1, Math.max(1, atTop(tokens, new Set(['=']), 1))).filter((token) => token.kind === 'identifier')
        for (const target of loop) if (target.text !== 'in') changed = spread(target.text, value, within) || changed
      }
      const mutated = mutatedBy(tokens, methods)
      if (mutated.length > 0) {
        const value = new Map(own)
        merge(value, taintOf(tokens))
        for (const target of mutated) changed = spread(target, value, within) || changed
      }
      // A call of one of the script's functions: what it changes, and what it is given, may read what it reads.
      for (let at = 0; at < tokens.length; at += 1) {
        const token = tokens[at]
        if (token.kind !== 'identifier' || tokens[at + 1]?.text !== '(' || !functions.has(token.text)) continue
        const given = new Map(own)
        merge(given, taintOf(tokens, at + 1, closing(tokens, at + 1) + 1))
        let called = calledWith.get(token.text)
        if (!called) {
          called = new Map()
          calledWith.set(token.text, called)
        }
        changed = merge(called, given) || changed
        const reads = new Map(given)
        merge(reads, functions.get(token.text), token.text)
        for (const target of writes.get(token.text) ?? []) changed = spread(target, reads, within) || changed
        for (const entry of callArguments(tokens, at + 1).args) {
          if (entry[0]?.kind === 'identifier') changed = spread(entry[0].text, reads, within) || changed
        }
        // A method's object: ``state.update()`` may change ``state``.
        if (tokens[at - 1]?.text === '.' && tokens[at - 2]?.kind === 'identifier') changed = spread(tokens[at - 2].text, reads, within) || changed
      }
    })
    if (!changed) break
  }

  const sinkOf = (index: number): Taint => {
    const taint = new Map(controlOf(index))
    merge(taint, taintOf(statements[index].tokens))
    return taint
  }
  const reached: Taint = new Map()
  const byLine = new Map<number, number>()
  statements.forEach((statement, index) => {
    const line = scriptLine(statement.line)
    if (line !== null && !byLine.has(line)) byLine.set(line, index)
    const tokens = statement.tokens
    for (let at = 0; at < tokens.length; at += 1) {
      const name = tokens[at].text
      if ((name === 'alertcondition' || name === 'alert') && tokens[at + 1]?.text === '(' && tokens[at - 1]?.text !== '.') {
        merge(reached, sinkOf(index))
        break
      }
    }
  })
  const atLine = (line: number): Taint => {
    const index = byLine.get(line)
    if (index === undefined) return new Map()
    const tokens = statements[index].tokens
    // An if's condition, when the candidate is what is done under it; the whole call when it is a plot.
    if (tokens[0]?.text === 'if') {
      const taint = new Map(controlOf(index))
      merge(taint, taintOf(tokens, 1))
      return taint
    }
    return sinkOf(index)
  }
  return { reached, atLine }
}

// ── what a rule maps ──

function declarationKind(tokens: Token[]): ScriptKind | null {
  const head = tokens.find((token) => token.kind === 'identifier')
  return head && ['indicator', 'strategy', 'library'].includes(head.text) && tokens[tokens.indexOf(head) + 1]?.text === '('
    ? (head.text as ScriptKind)
    : null
}

interface Declared {
  alerts: (DeclaredAlert & { condition: string; column: number })[]
  calls: (AlertCall & { freq: string | null; column: number })[]
  plots: string[]
}

function declaredOf(tokens: Token[]): Declared {
  const declared: Declared = { alerts: [], calls: [], plots: [] }
  for (let index = 0; index < tokens.length - 1; index += 1) {
    const token = tokens[index]
    if (token.kind !== 'identifier' || tokens[index + 1].text !== '(' || tokens[index - 1]?.text === '.') continue
    if (token.text === 'alertcondition') {
      const { args } = callArguments(tokens, index + 1)
      const title = argument(args, 'title', 1)
      declared.alerts.push({
        title: literal(title),
        message: literal(argument(args, 'message', 2)),
        line: token.line,
        column: token.column,
        condition: codeText(argument(args, 'condition', 0) ?? []),
      })
    } else if (token.text === 'alert') {
      const { args } = callArguments(tokens, index + 1)
      const freq = argument(args, 'freq', 1)
      declared.calls.push({
        message: literal(argument(args, 'message', 0)),
        line: token.line,
        column: token.column,
        freq: freq ? codeText(freq) : null,
      })
    } else if (token.text in PLOT_TITLES) {
      const title = literal(argument(callArguments(tokens, index + 1).args, 'title', PLOT_TITLES[token.text]))
      if (title !== null && !declared.plots.includes(title)) declared.plots.push(title)
    }
  }
  return declared
}

/** The events a script draws, or acts on under an if, which a rule could be raised by. */
function candidatesOf(statements: Statement[], declared: Declared): Omit<AlertCandidate, 'risks'>[] {
  const found: Omit<AlertCandidate, 'risks'>[] = []
  const conditions = new Set(declared.alerts.map((alert) => alert.condition.replace(/\s+/g, '')))
  const titles = new Set(declared.alerts.map((alert) => alert.title).filter((title): title is string => title !== null))
  const add = (candidate: Omit<AlertCandidate, 'risks'>) => {
    const key = candidate.condition.replace(/\s+/g, '')
    if (!key || conditions.has(key) || titles.has(candidate.title) || found.length >= MAX_CANDIDATES) return
    conditions.add(key)
    // Two events drawn under one title (a copy left as it was) are two alerts: each its own title.
    let title = candidate.title
    for (let suffix = 2; found.some((other) => other.title === title); suffix += 1) title = `${candidate.title} ${suffix}`
    found.push({ ...candidate, title })
  }
  let unnamed = 0
  const titleOr = (title: string | null, condition: Token[]) => {
    if (title) return title
    if (condition.length === 1 && condition[0].kind === 'identifier') return condition[0].text
    unnamed += 1
    return `Event ${unnamed}`
  }
  statements.forEach((statement, index) => {
    if (statement.level !== 0) return
    const tokens = statement.tokens
    const head = tokens[0]
    if (!head || head.kind !== 'identifier') return
    if ((head.text === 'plotshape' || head.text === 'plotchar') && tokens[1]?.text === '(') {
      const { args } = callArguments(tokens, 1)
      const series = argument(args, 'series', 0)
      if (!series || series.length === 0) return
      // plotchar(value, "Title", "") only shows a value in the data window: no event.
      if (head.text === 'plotchar' && literal(argument(args, 'char', 2)) === '') return
      const condition = ternaryCondition(series) ?? series
      const title = literal(argument(args, 'title', 1))
      add({ title: titleOr(title, condition), condition: codeText(condition), call: head.text, line: statement.line,
        at: { line: statement.lastLine, where: 'after' } })
    } else if ((head.text === 'bgcolor' || head.text === 'barcolor') && tokens[1]?.text === '(') {
      const { args } = callArguments(tokens, 1)
      const condition = ternaryCondition(argument(args, 'color', 0) ?? [])
      if (!condition) return
      const title = literal(argument(args, 'title', 4))
      add({ title: titleOr(title, condition), condition: codeText(condition), call: head.text, line: statement.line,
        at: { line: statement.lastLine, where: 'after' } })
    } else if ((head.text === 'plotcandle' || head.text === 'plotbar') && tokens[1]?.text === '(') {
      const { args } = callArguments(tokens, 1)
      const title = literal(argument(args, 'title', 4))
      for (const colour of ['color', 'bordercolor', 'wickcolor']) {
        const named = args.find((entry) => isNamed(entry, colour))
        const condition = named ? ternaryCondition(named.slice(2)) : null
        if (condition) {
          add({ title: titleOr(title, condition), condition: codeText(condition), call: head.text, line: statement.line,
            at: { line: statement.lastLine, where: 'after' } })
        }
      }
    } else if (head.text === 'if') {
      // An alert() or an order directly under a top-level if: the if's condition, read before the block changes it.
      const condition = tokens.slice(1)
      for (let child = index + 1; child < statements.length && statements[child].level > 0; child += 1) {
        const inner = statements[child]
        if (inner.parent !== index) continue
        const first = inner.tokens[0]?.text
        if (first === 'alert' && inner.tokens[1]?.text === '(') {
          const message = literal(argument(callArguments(inner.tokens, 1).args, 'message', 0))
          add({ title: titleOr(message, condition), condition: codeText(condition), call: 'alert', line: inner.line,
            at: { line: statement.line, where: 'before' } })
        } else if (first === 'strategy' && inner.tokens[2]?.text === 'entry' && inner.tokens[3]?.text === '(') {
          const id = literal(argument(callArguments(inner.tokens, 3).args, 'id', 0))
          add({ title: titleOr(id, condition), condition: codeText(condition), call: 'strategy.entry', line: inner.line,
            at: { line: statement.line, where: 'before' } })
        }
      }
    }
  })
  return found
}

// ── what is said ──

function listed(names: string[]): string {
  const unique = [...new Set(names)]
  if (unique.length <= 1) return unique[0] ?? ''
  return `${unique.slice(0, -1).join(', ')} and ${unique[unique.length - 1]}`
}

/** A name as the script wrote it: prepare.ts inlines a library's ``fn`` as ``Name__fn``. */
function written(text: string): string {
  return text.replace(/\b[A-Za-z_]\w*__(\w+)\b/g, '$1')
}

function plural(count: number, one: string, many = `${one}s`): string {
  return `${count} ${count === 1 ? one : many}`
}

interface Hit {
  names: string[]
  places: Place[]
  count: number
  excerpt: string | null
}

const REPAINT_KINDS: SourceKind[] = ['bar-state', 'bar-end', 'clock', 'random', 'visible-range', 'other-series', 'lookahead', 'strategy-state']

/** The verdict of findings, worst first: what a rule cannot be, what repaints, may repaint, is to change. */
export function verdictOf(findings: Pick<RuleFinding, 'id' | 'level'>[]): RuleVerdict {
  const blocking = findings.filter((finding) => finding.level === 'blocks')
  if (blocking.length > 0) return blocking.every((finding) => NEEDS_ALERTS.has(finding.id)) ? 'needs_alerts' : 'blocked'
  if (findings.some((finding) => finding.level === 'repaints')) return 'repaints'
  if (findings.some((finding) => finding.level === 'risk')) return 'may_repaint'
  if (findings.some((finding) => finding.level === 'change')) return 'to_change'
  return 'ready'
}
const NEEDS_ALERTS = new Set(['no-alerts', 'alert-messages', 'strategy'])

/**
 * The rule check of a script as written: ``prepared`` is it with its libraries inlined, when that could be done,
 * ``failure`` why it could not (a library WATA does not have...), read on the script alone then.
 */
// What a run gives otherwise at its last bar, as live has it at every bar: the probe of the run test.
const LIVE_AT_EVERY_BAR: Record<string, string> = {
  'barstate.islast': 'true',
  'barstate.isrealtime': 'true',
  'barstate.islastconfirmedhistory': 'true',
  'barstate.ishistory': 'false',
  'session.islastbar': 'true',
  'session.islastbar_regular': 'true',
  last_bar_index: 'bar_index',
  last_bar_time: 'time',
}

/**
 * The script with every bar read as the last of its run, as live reads each new bar: what a run gives otherwise at
 * its last bar written as its value there (`barstate.islast` true, `last_bar_index` the bar's own index...), the rest
 * as written. One run of it over a day shows the bars where live may alert, which the run test on recorded bars ends
 * runs at (src/research/study.py): it only chooses where to look, what a run ending there raises decides. Null when
 * it reads none of them. Its own lines alone, not its libraries'.
 */
export function liveProbe(source: string): string | null {
  const tokens = significant(tokenize(source))
  const replaced: { start: number; end: number; text: string }[] = []
  tokens.forEach((token, index) => {
    if (token.kind !== 'identifier' || tokens[index - 1]?.text === '.') return
    const field = tokens[index + 1]?.text === '.' && tokens[index + 2]?.kind === 'identifier' ? tokens[index + 2] : null
    const name = field ? `${token.text}.${field.text}` : token.text
    const value = LIVE_AT_EVERY_BAR[name]
    if (value === undefined) return
    const last = field ?? token
    replaced.push({ start: token.start, end: last.start + last.text.length, text: value })
  })
  if (replaced.length === 0) return null
  let probe = source
  for (const entry of replaced.reverse()) probe = probe.slice(0, entry.start) + entry.text + probe.slice(entry.end)
  return probe
}

/** The rule check, or null when it cannot be made of this source: a fault of the check never costs a page or the editor its other marks. */
export function triedRuleCheck(source: string, prepared: PreparedScript | null = null, failure: PrepareError | null = null): RuleCheck | null {
  try {
    return ruleCheckOf(source, prepared, failure)
  } catch (error) {
    console.error('The rule check could not be made of this source', error)
    return null
  }
}

export function ruleCheckOf(source: string, prepared: PreparedScript | null = null, failure: PrepareError | null = null): RuleCheck {
  const check: RuleCheck = {
    version: RULE_CHECK_VERSION, kind: null, pineVersion: null, alerts: [], alertCalls: [], candidates: [], plots: [],
    wholeBecause: null, findings: [], verdict: 'blocked',
  }
  const finding = (entry: Omit<RuleFinding, 'places' | 'count' | 'excerpt' | 'through'> & Partial<RuleFinding>) => {
    check.findings.push({ places: [], count: 1, excerpt: null, through: null, ...entry })
  }
  try {
    check.pineVersion = checkedVersion(source)
  } catch (error) {
    if (!(error instanceof PrepareError)) throw error
    finding({ id: 'version', area: 'wata', level: 'blocks', message: `${error.message}.`, fix: 'Write it in Pine Script v5 or v6.',
      places: error.line ? [{ file: null, line: error.line, column: error.column ?? 1 }] : [] })
    return check
  }
  const own = significant(tokenize(source))
  check.kind = prepared?.kind ?? declarationKind(own)
  if (check.kind === 'library') {
    finding({ id: 'library', area: 'wata', level: 'blocks', message: 'A library: a rule runs an indicator or a strategy that imports it.', fix: null })
    check.verdict = verdictOf(check.findings)
    return check
  }
  if (failure) {
    const place = failure.line && failure.file === SCRIPT_FILE ? [{ file: null, line: failure.line, column: failure.column ?? 1 }] : []
    finding({ id: 'library-missing', area: 'wata', level: 'blocks', message: `${failure.message}.`, fix: null, places: place })
  }

  // The script's own lines: its alerts and the events it draws, as written.
  const ownStatements = statementsOf(tokenize(source))
  const declared = declaredOf(own)
  check.alerts = declared.alerts.map(({ title, message, line }) => ({ title, message, line }))
  check.alertCalls = declared.calls.map(({ message, line }) => ({ message, line }))
  check.plots = declared.plots
  const candidates = candidatesOf(ownStatements, declared)

  // What may repaint, followed through the script with its libraries inlined.
  const text = prepared?.source ?? source
  const all = tokenize(text)
  const lines = text.split('\n')
  const place = (token: Token): Place => {
    const origin = prepared ? originOf(prepared, token.line) : null
    if (!origin) return { file: null, line: token.line, column: token.column }
    return { file: origin.file === SCRIPT_FILE ? null : origin.file, line: origin.line, column: token.column }
  }
  const scriptLine = (line: number): number | null => {
    if (!prepared) return line
    const origin = originOf(prepared, line)
    return origin && origin.file === SCRIPT_FILE ? origin.line : null
  }
  const tokens = significant(all)
  const reading: Reading = { text, lines, tokens, statements: statementsOf(all), sources: sourcesOf(tokens), place }
  const trace = traceOf(reading, scriptLine)
  check.candidates = candidates.map((candidate) => ({
    ...candidate,
    risks: [...trace.atLine(candidate.at.where === 'before' ? candidate.at.line : candidate.line).keys()].filter((kind) => REPAINT_KINDS.includes(kind)),
  }))
  // The alerts decide what a rule sends: with none yet, what would decide them is what they would be added on.
  const reached = new Map(trace.reached)
  if (declared.alerts.length === 0 && declared.calls.length === 0) {
    for (const candidate of candidates) merge(reached, trace.atLine(candidate.at.where === 'before' ? candidate.at.line : candidate.line))
  }

  const hits = new Map<SourceKind, Hit>()
  for (const [token, source] of reading.sources) {
    let hit = hits.get(source.kind)
    if (!hit) {
      const text = (lines[token.line - 1] ?? '').trim()
      const code = written(text)
      hit = { names: [], places: [], count: 0, excerpt: code.length > EXCERPT_CHARS ? `${code.slice(0, EXCERPT_CHARS - 1)}…` : code }
      hits.set(source.kind, hit)
    }
    hit.count += 1
    if (!hit.names.includes(source.name)) hit.names.push(source.name)
    if (hit.places.length < MAX_PLACES) hit.places.push(place(token))
  }
  const said = (kind: SourceKind, entry: { level: FindingLevel; area: FindingArea; message: string; fix: string | null }) => {
    const hit = hits.get(kind)
    if (!hit) return
    const path = reached.get(kind)
    finding({ id: kind, ...entry, places: hit.places, count: hit.count, excerpt: hit.excerpt,
      through: path && path.through.length > 0 ? path.through.map(written) : null })
  }
  const names = (kind: SourceKind) => listed(hits.get(kind)?.names ?? [])
  const reaches = (kind: SourceKind) => reached.has(kind)
  const drawings = (kind: SourceKind) => `Reads ${names(kind)} for its drawings only: its alerts do not depend on it.`

  // Repainting.
  said('bar-state', reaches('bar-state')
    ? { level: 'risk', area: 'repaint', message: `Its alerts read ${names('bar-state')}, which a run gives otherwise at its last bar: live, each new bar is the last of its run; in a backtest, only the engine day's last bar is.`, fix: 'Keep it for drawings; the run test on recorded bars says whether its alerts differ.' }
    : { level: 'note', area: 'repaint', message: drawings('bar-state'), fix: null })
  said('bar-end', reaches('bar-end')
    ? { level: 'risk', area: 'repaint', message: `Its alerts read ${names('bar-end')}: the current bar live, the engine day's last bar in a backtest. A window such as \`last_bar_index - bar_index <= 2000\` hides early signals from backtests.`, fix: 'Replace such a window by `true`, or by an input.' }
    : { level: 'note', area: 'repaint', message: drawings('bar-end'), fix: null })
  said('clock', reaches('clock')
    ? { level: 'repaints', area: 'repaint', message: 'Its alerts read timenow, the moment each run is made: no backtest, made later, can give what live gave.', fix: 'Use the bar\'s own time (`time`, `time_close`).' }
    : { level: 'note', area: 'repaint', message: drawings('clock'), fix: null })
  said('random', reaches('random')
    ? { level: 'repaints', area: 'repaint', message: 'Its alerts read math.random: they change from one run to the next, so no backtest repeats them.', fix: 'Give it a seed, or decide without it.' }
    : { level: 'note', area: 'repaint', message: drawings('random'), fix: null })
  said('visible-range', reaches('visible-range')
    ? { level: 'risk', area: 'repaint', message: `Its alerts read ${names('visible-range')}, the chart's visible range: WATA runs it with no chart.`, fix: 'Decide without it.' }
    : { level: 'note', area: 'repaint', message: drawings('visible-range'), fix: null })
  said('lookahead', reaches('lookahead')
    ? { level: 'repaints', area: 'repaint', message: 'Its alerts read another series with lookahead_on and no offset: a backtest sees each of its bars before it is over, which live never can.', fix: 'Leave lookahead off, and hold the last value: `fixnan(request.security(sym, tf, expr))`.' }
    : { level: 'note', area: 'repaint', message: drawings('lookahead'), fix: null })
  said('other-series', reaches('other-series')
    ? { level: 'risk', area: 'repaint', message: `Its alerts read another series (${names('other-series')}): live, it holds only bars that are over, so a bar of a longer timeframe still open is missing (na), where a backtest has the whole day's.`, fix: 'Hold its last value, `fixnan(request.security(sym, tf, expr))`: live and a backtest then read the same. The run test on recorded bars says whether its alerts differ.' }
    : { level: 'note', area: 'repaint', message: drawings('other-series'), fix: null })
  if (reaches('strategy-state')) {
    said('strategy-state', { level: 'risk', area: 'repaint', message: `Its alerts read the strategy's own state (${names('strategy-state')}), which its orders make, and orders are not signals on WATA.`, fix: 'Decide from the market alone.' })
  }
  said('isconfirmed', { level: 'note', area: 'repaint', message: `Reads ${names('isconfirmed')}, always true on WATA, whose bars are all over: it changes nothing, but makes each new bar run the whole script.`, fix: 'Leave it out.' })

  // What WATA serves otherwise.
  said('no-data', reaches('no-data')
    ? { level: 'blocks', area: 'wata', message: `Its alerts read ${names('no-data')}, which WATA does not have: na here, so they never fire as written.`, fix: 'Decide without it.' }
    : { level: 'note', area: 'wata', message: `${drawings('no-data')} It is na on WATA.`, fix: null })
  said('mintick', reaches('mintick')
    ? { level: 'change', area: 'wata', message: 'Its alerts read syminfo.mintick, na on WATA: whatever they compute from it is na too.', fix: 'Use the asset\'s tick as an input (0.1 on Capital.com\'s US100).' }
    : { level: 'note', area: 'wata', message: `${drawings('mintick')} It is na on WATA.`, fix: null })
  said('syminfo', reaches('syminfo')
    ? { level: 'change', area: 'wata', message: `Its alerts read ${names('syminfo')}, which WATA does not give: na here.`, fix: 'Use a constant or an input.' }
    : { level: 'note', area: 'wata', message: `${drawings('syminfo')} It is na on WATA.`, fix: null })
  said('utc-time', reaches('utc-time')
    ? { level: 'change', area: 'wata', message: `Its alerts read the bar's time or day with no time zone (${names('utc-time')}): WATA's days are UTC days, so it is read in UTC, not in the exchange's zone.`, fix: 'Give the zone: `hour(time, "America/New_York")`, `dayofmonth(time, "Europe/Paris")`, `time(timeframe.period, "0930-1600", "America/New_York")`.' }
    : { level: 'note', area: 'wata', message: `Reads the bar's time or day with no time zone (${names('utc-time')}), for its drawings only: WATA reads it in UTC.`, fix: null })
  if (reaches('volume')) {
    const read = names('volume') === 'volume' ? 'volume' : `volume (${names('volume')})`
    said('volume', { level: 'note', area: 'wata', message: `Its alerts read ${read}: on WATA, the count of price changes in each ten seconds, 0 on many bars, not traded volume.`, fix: null })
  }

  // The series a rule cannot read live.
  for (let index = 0; index + 3 < tokens.length; index += 1) {
    if (tokens[index].text !== 'request' || tokens[index + 1].text !== '.' || tokens[index + 2].text !== 'security' || tokens[index + 3].text !== '(') continue
    const symbol = literal(argument(callArguments(tokens, index + 3).args, 'symbol', 0))
    if (symbol === null || symbol === '') continue
    const ticker = parseTicker(symbol)
    const at = place(tokens[index])
    const excerpt = (lines[tokens[index].line - 1] ?? '').trim()
    if (!ticker) {
      finding({ id: 'ticker', area: 'wata', level: 'blocks', message: `Reads "${symbol}", none of WATA's assets: the rule would fail.`,
        fix: 'Name one of WATA\'s assets: CAPITAL_US100, with .ASK for the ask, .R60 for range bars.', places: [at], excerpt })
    } else if (ticker.provider !== 'capital') {
      finding({ id: 'live-ticker', area: 'wata', level: 'blocks', message: `Reads "${symbol}": WATA's backtests have it, but live a rule reads only Capital.com's series, and would fail.`,
        fix: 'Read Capital.com\'s: CAPITAL_US100.', places: [at], excerpt })
    }
  }

  // The alerts a rule maps.
  const titled = declared.alerts.filter((alert) => alert.title !== null)
  const untitled = declared.alerts.filter((alert) => alert.title === null)
  if (check.kind === 'strategy') {
    finding(declared.alerts.length + declared.calls.length > 0
      ? { id: 'strategy', area: 'alerts', level: 'change', message: 'A strategy(): its orders are not signals, only its alerts are, and each new bar runs it whole.', fix: 'Make it an indicator() with an alertcondition() on each entry and exit.' }
      : { id: 'strategy', area: 'alerts', level: 'blocks', message: 'A strategy(): its orders are not signals, and it has no alert to raise one.', fix: 'Make it an indicator() with an alertcondition() on each entry and exit.' })
  } else if (declared.alerts.length === 0 && declared.calls.length === 0) {
    const events = check.candidates.length > 0
      ? ` It draws ${plural(check.candidates.length, 'event')} an alert could be raised on: ${listed(check.candidates.map((candidate) => `“${candidate.title}”`))}.`
      : ''
    finding({ id: 'no-alerts', area: 'alerts', level: 'blocks', message: `Nothing in it can become a signal: it has no alertcondition() and no alert().${events}`,
      fix: 'Add an alertcondition() on each event a rule should act on.' })
  }
  for (const alert of untitled) {
    finding({ id: 'title-expression', area: 'alerts', level: 'change', message: 'An alertcondition() whose title is not written as text: a rule maps alerts by title, so it can never map this one.',
      fix: 'Write its title as text.', places: [{ file: null, line: alert.line, column: alert.column }] })
  }
  const seen = new Map<string, number>()
  for (const alert of titled) seen.set(alert.title ?? '', (seen.get(alert.title ?? '') ?? 0) + 1)
  for (const [title, times] of seen) {
    if (times < 2) continue
    finding({ id: 'title-duplicate', area: 'alerts', level: 'change', message: `${times} alertconditions are titled “${title}”: a rule maps them alike, and live sends only one of them a bar.`,
      fix: 'Give each its own title.', places: titled.filter((alert) => alert.title === title).map((alert) => ({ file: null, line: alert.line, column: alert.column })), count: times })
  }
  for (const alert of [...declared.alerts, ...declared.calls]) {
    for (const match of (alert.message ?? '').matchAll(/\{\{\s*plot\("([^"]*)"\)\s*\}\}/g)) {
      if (check.plots.includes(match[1])) continue
      finding({ id: 'message-plot', area: 'alerts', level: 'change', message: `A message reads {{plot("${match[1]}")}}, and no plot is titled so: it is left as it is.`,
        fix: check.plots.length > 0 ? `Name one of its plots: ${listed(check.plots.map((title) => `“${title}”`))}.` : 'Name one of its plots.',
        places: [{ file: null, line: alert.line, column: alert.column }] })
    }
  }
  const plain = declared.calls.filter((call) => call.message !== null && messageAction(call.message) === null)
  if (plain.length > 0) {
    const examples = listed(plain.slice(0, 3).map((call) => `“${call.message}”`))
    // Its only alerts, and none of its alert() calls sends WATA's JSON, or a message made at run time.
    const only = declared.alerts.length === 0 && plain.length === declared.calls.length
    finding({ id: 'alert-messages', area: 'alerts', level: only && check.kind !== 'strategy' ? 'blocks' : 'note',
      message: only
        ? `Its alert() calls send ${examples}: as messages they make no signal, WATA's being JSON such as {"action": "long"}.`
        : `Its alert() calls send ${examples}, which make no signal: a rule maps its alertconditions.`,
      fix: only ? 'Add alertconditions, or write the messages as WATA\'s JSON.' : null,
      places: plain.slice(0, MAX_PLACES).map((call) => ({ file: null, line: call.line, column: call.column })), count: plain.length })
  }
  const frequent = declared.calls.filter((call) => call.freq !== null && !/freq_once_per_bar_close/.test(call.freq))
  if (frequent.length > 0) {
    finding({ id: 'alert-freq', area: 'alerts', level: 'note', message: `Its alert() calls ask ${listed(frequent.map((call) => call.freq ?? ''))}: WATA reads alerts once each bar is over, never inside one.`,
      fix: null, places: frequent.slice(0, MAX_PLACES).map((call) => ({ file: null, line: call.line, column: call.column })), count: frequent.length })
  }

  // How the script is written.
  const declaration = own.findIndex((token, index) => token.text === 'indicator' && own[index + 1]?.text === '(')
  if (declaration !== -1 && check.kind === 'indicator') {
    const timeframe = literal(argument(callArguments(own, declaration + 1).args, 'timeframe', null))
    if (timeframe) {
      const token = own[declaration]
      finding({ id: 'indicator-timeframe', area: 'wata', level: 'change', message: `It asks to run on other bars (indicator(timeframe = "${timeframe}")): WATA runs it on the rule's own bars.`,
        fix: 'Leave it out, and choose the bars in the rule.', places: [{ file: null, line: token.line, column: token.column }] })
    }
  }
  const pivots = own.filter((token, index) => token.text === 'ta' && own[index + 1]?.text === '.' && /^pivot(?:high|low)$/.test(own[index + 2]?.text ?? '') && own[index + 3]?.text === '(')
  if (pivots.length > 0 && declared.alerts.length + declared.calls.length + check.candidates.length > 0) {
    const at = own.indexOf(pivots[0])
    const args = callArguments(own, at + 3).args.filter((entry) => !isNamed(entry))
    const right = argument(callArguments(own, at + 3).args, 'rightbars', args.length - 1)
    const bars = right ? codeText(right) : null
    const late = bars === null ? 'some bars' : /^\d+$/.test(bars) ? plural(Number(bars), 'bar') : `\`${bars}\` bars`
    finding({ id: 'pivot-lag', area: 'repaint', level: 'note',
      message: `It finds swings with ta.${own[at + 2].text}(), each known ${late} after it: a signal from one comes that late, live as in a backtest, while what it draws is drawn back at the swing.`,
      fix: null, places: pivots.slice(0, MAX_PLACES).map((token) => ({ file: null, line: token.line, column: token.column })), count: pivots.length })
  }
  const backwards = own.filter((token, index) => token.text === 'offset' && own[index + 1]?.text === '=' && own[index + 2]?.text === '-')
  if (backwards.length > 0) {
    finding({ id: 'offset-back', area: 'repaint', level: 'note', message: 'It draws some plots back in time (offset = -…): the chart shows a value at a bar before it was known there.',
      fix: null, places: backwards.slice(0, MAX_PLACES).map((token) => ({ file: null, line: token.line, column: token.column })), count: backwards.length })
  }
  const varips = own.filter((token) => token.text === 'varip')
  if (varips.length > 0) {
    finding({ id: 'varip', area: 'wata', level: 'note', message: 'It declares varip: every bar WATA gives is over, so it behaves as var.', fix: null,
      places: varips.slice(0, MAX_PLACES).map((token) => ({ file: null, line: token.line, column: token.column })), count: varips.length })
  }
  check.wholeBecause = wholeBecause({ source: text, kind: check.kind ?? 'indicator' })
  if (check.wholeBecause) {
    finding({ id: 'whole-runs', area: 'wata', level: 'note', message: `Each new bar runs it whole: ${check.wholeBecause}. That is slower than going on from the bar before; the run test measures what a run costs.`, fix: null })
  }
  check.verdict = verdictOf(check.findings)
  return check
}

// ── alerts written in ──

function pineString(text: string): string {
  return `"${text.replace(/\\/g, '\\\\').replace(/"/g, '\\"')}"`
}

/**
 * The source with an alertcondition() on each of the chosen candidates, titled after it (made unique against
 * ``taken``, the titles the script has): after a plot's statement, before an if whose block may change its
 * condition. Each line says it was added for a signal rule.
 */
export function withAlerts(source: string, chosen: AlertCandidate[], taken: string[] = []): string {
  if (chosen.length === 0) return source
  const titles = new Set(taken)
  const inserts = chosen.map((candidate, order) => {
    let title = candidate.title
    for (let suffix = 2; titles.has(title); suffix += 1) title = `${candidate.title} ${suffix}`
    titles.add(title)
    const quoted = pineString(title)
    return { at: candidate.at, order, text: `alertcondition(${candidate.condition}, ${quoted}, ${quoted}) // added for a signal rule` }
  })
  // From the bottom, so that the lines above keep their numbers; after a line before the line itself.
  inserts.sort((a, b) => b.at.line - a.at.line || (a.at.where === b.at.where ? b.order - a.order : a.at.where === 'after' ? -1 : 1))
  const lines = source.split('\n')
  for (const insert of inserts) lines.splice(insert.at.where === 'after' ? insert.at.line : insert.at.line - 1, 0, insert.text)
  return lines.join('\n')
}
