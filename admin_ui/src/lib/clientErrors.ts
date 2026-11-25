import type { ErrorReport, ErrorReporter } from 'ioiti-ui'

import { api } from './api'
import type { ClientErrorBody } from './types'

/** The longest each text goes out, as `src/admin/client_errors.py` takes them. */
export const MOST_CHARACTERS = { name: 200, message: 2000, stack: 8000 } as const

/** The same error, on the same page, is said once in this time. */
export const SAME_ERROR_MS = 60_000

/** How many errors are remembered for that, the oldest forgotten first. */
const REMEMBERED = 50

function cut(text: string, most: number): string {
  return text.length > most ? `${text.slice(0, most - 1)}…` : text
}

/**
 * What a report says of an error: its name, message and stack for an Error; a route's answer by its status (React
 * Router's error response); anything else as text. The page's path alone, never its query.
 */
export function describeError(error: unknown, report: ErrorReport, path: string): ClientErrorBody {
  let name = 'Error'
  let message = ''
  let stack: string | undefined
  if (error instanceof Error) {
    name = error.name || 'Error'
    message = error.message
    stack = error.stack
  } else if (error && typeof error === 'object' && 'status' in error) {
    const answer = error as { status: unknown; statusText?: unknown }
    name = `HTTP ${String(answer.status)}`
    message = typeof answer.statusText === 'string' ? answer.statusText : ''
  } else {
    message = String(error)
  }
  const body: ClientErrorBody = {
    source: report.source,
    path: cut(path.startsWith('/') ? path : `/${path}`, 500),
    name: cut(name, MOST_CHARACTERS.name),
    message: cut(message, MOST_CHARACTERS.message),
  }
  if (stack) body.stack = cut(stack, MOST_CHARACTERS.stack)
  if (report.componentStack) body.component_stack = cut(report.componentStack, MOST_CHARACTERS.stack)
  return body
}

/**
 * What IoitiProvider's `onError` is given: each error the shell's boundary or the router's error page caught goes to
 * the admin's log (the Logs page, Admin), the same one on the same page once a minute. A report that cannot be sent
 * is said nowhere: the page already shows what failed, and a report never throws.
 */
export function errorReporter(
  send: (body: ClientErrorBody) => Promise<unknown> = api.reportClientError,
  now: () => number = Date.now,
  path: () => string = () => window.location.pathname,
): ErrorReporter {
  const said = new Map<string, number>()
  return (error, report) => {
    try {
      const body = describeError(error, report, path())
      const key = `${body.path}\n${body.name}\n${body.message}`
      const at = now()
      const before = said.get(key)
      if (before !== undefined && at - before < SAME_ERROR_MS) return
      said.delete(key)
      said.set(key, at)
      if (said.size > REMEMBERED) said.delete(said.keys().next().value as string)
      void send(body).catch(() => undefined)
    } catch {
      // A report must never be what breaks the page further.
    }
  }
}
