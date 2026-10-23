import type { ServiceStatus } from '@/lib/types'

const ACTION_LABELS: Record<string, string> = {
  long: 'Long',
  short: 'Short',
  'close-long': 'Close long',
  'close-short': 'Close short',
  'close-position': 'Close all',
  daily_stats: 'Daily stats',
}

export function actionLabel(action: string | null | undefined): string {
  if (!action) return '—'
  return ACTION_LABELS[action] ?? action
}

/** A service's state in a word: beside its colour, never instead of it. */
export const SERVICE_STATUS_LABEL: Record<ServiceStatus, string> = {
  ok: 'Up',
  warning: 'Degraded',
  down: 'Down',
  unknown: 'Unknown',
  disabled: 'Disabled',
}
