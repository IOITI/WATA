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
