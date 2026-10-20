// What a script offers a signal rule to map, read without running it: the
// titles and messages of its alertcondition() calls, whether it calls alert(),
// and the titles of its plots. Only titles written as text are known: one made
// of an expression is not.

import { significant, tokenize } from './lexer'
import type { Token } from './lexer'

export interface AlertOutline {
  title: string
  message: string | null
}

export interface ScriptOutline {
  alerts: AlertOutline[]
  /** The script calls alert(): its messages are signals or nothing, all alike. */
  alertCalls: boolean
  plots: string[]
}

const ESCAPES: Record<string, string> = { n: '\n', t: '\t' }

function text(token: Token): string {
  return token.text.slice(1, token.text.endsWith(token.text[0]) && token.text.length > 1 ? -1 : undefined).replace(
    /\\(.)/g,
    (_, char: string) => ESCAPES[char] ?? char,
  )
}

/** The arguments of the call whose "(" is at ``open``: each a list of tokens, and where the call ends. */
function callArguments(tokens: Token[], open: number): { args: Token[][]; end: number } {
  const args: Token[][] = [[]]
  let depth = 0
  for (let index = open; index < tokens.length; index += 1) {
    const token = tokens[index]
    if (token.kind === 'operator' && (token.text === '(' || token.text === '[')) {
      depth += 1
      if (depth === 1) continue
    } else if (token.kind === 'operator' && (token.text === ')' || token.text === ']')) {
      depth -= 1
      if (depth === 0) return { args: args.filter((arg) => arg.length > 0), end: index }
    } else if (depth === 1 && token.kind === 'operator' && token.text === ',') {
      args.push([])
      continue
    }
    args[args.length - 1].push(token)
  }
  return { args: args.filter((arg) => arg.length > 0), end: tokens.length }
}

/** The text of the argument named ``name``, or of the one at ``position``: null unless it is a string. */
function stringArgument(args: Token[][], name: string, position: number): string | null {
  const named = args.find((arg) => arg.length >= 3 && arg[0].kind === 'identifier' && arg[0].text === name && arg[1].text === '=')
  const value = named ? named.slice(2) : args.filter((arg) => !(arg.length >= 2 && arg[1].text === '='))[position]
  return value && value.length === 1 && value[0].kind === 'string' ? text(value[0]) : null
}

export function scriptOutline(source: string): ScriptOutline {
  const tokens = significant(tokenize(source))
  const alerts: AlertOutline[] = []
  const plots: string[] = []
  let alertCalls = false
  for (let index = 0; index < tokens.length - 1; index += 1) {
    const token = tokens[index]
    const next = tokens[index + 1]
    if (token.kind !== 'identifier' || next.text !== '(') continue
    // A method or namespace call (array.alert, ...) is not Pine's own.
    if (index > 0 && tokens[index - 1].text === '.') continue
    if (token.text === 'alert') {
      alertCalls = true
      continue
    }
    if (token.text !== 'alertcondition' && token.text !== 'plot') continue
    const { args } = callArguments(tokens, index + 1)
    if (token.text === 'plot') {
      const title = stringArgument(args, 'title', 1)
      if (title && !plots.includes(title)) plots.push(title)
      continue
    }
    const title = stringArgument(args, 'title', 1)
    if (title && !alerts.some((alert) => alert.title === title)) {
      alerts.push({ title, message: stringArgument(args, 'message', 2) })
    }
  }
  return { alerts, alertCalls, plots }
}

/** The action WATA's webhook JSON in a message names, when it is one WATA sends. */
export function messageAction(message: string | null): 'long' | 'short' | 'close-position' | null {
  if (!message) return null
  try {
    const content: unknown = JSON.parse(message.replace(/\{\{[^}]*\}\}/g, '0'))
    const action = content && typeof content === 'object' ? (content as { action?: unknown }).action : null
    return action === 'long' || action === 'short' || action === 'close-position' ? action : null
  } catch {
    return null
  }
}
