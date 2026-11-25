import { screen, waitFor } from '@testing-library/react'

/**
 * What a page says in an alert. ioiti-ui's toasts keep their two live regions from the start (0.25), one of them an
 * empty `role="alert"` that a toast of an error fills: under the shell's providers, the alert that says something is
 * the page's.
 */
export function queryAlert(): HTMLElement | null {
  const said = screen.queryAllByRole('alert').filter((alert) => alert.textContent?.trim())
  if (said.length > 1) throw new Error(`${said.length} alerts say something: ${said.map((alert) => alert.textContent).join(' | ')}`)
  return said[0] ?? null
}

export function getAlert(): HTMLElement {
  const alert = queryAlert()
  if (!alert) throw new Error('No alert says anything')
  return alert
}

export function findAlert(): Promise<HTMLElement> {
  return waitFor(getAlert)
}
