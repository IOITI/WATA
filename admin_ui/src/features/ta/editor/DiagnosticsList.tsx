import { AlertCircle, AlertTriangle, Info, LoaderCircle } from 'lucide-react'

import { cn } from '@/lib/utils'

import { checkSummary } from './diagnostics'
import type { Diagnostic, Severity } from './diagnostics'

const ICON: Record<Severity, typeof AlertCircle> = { error: AlertCircle, warning: AlertTriangle, info: Info }
const TONE: Record<Severity, string> = {
  error: 'text-negative',
  warning: 'text-warning',
  info: 'text-muted-foreground',
}

/** What the checks found, under the editor: line, message and which check said it. */
export function DiagnosticsList({ diagnostics, checking }: { diagnostics: Diagnostic[]; checking: boolean }) {
  return (
    <section aria-label="Problems" className="rounded-xl border border-border bg-surface/50">
      <header className="flex items-center justify-between gap-2 border-b border-border px-3 py-2 text-xs">
        <span className="font-medium">Problems</span>
        <span className="flex items-center gap-1.5 text-muted-foreground" aria-live="polite">
          {checking && <LoaderCircle className="size-3.5 animate-spin" aria-hidden />}
          {checking ? 'Checking…' : checkSummary(diagnostics)}
        </span>
      </header>
      {diagnostics.length > 0 && (
        <ul className="max-h-56 divide-y divide-border overflow-auto text-xs">
          {diagnostics.map((diagnostic, index) => {
            const Icon = ICON[diagnostic.severity]
            return (
              <li key={`${diagnostic.line}:${diagnostic.column}:${index}`} className="flex gap-2 px-3 py-2">
                <Icon className={cn('mt-0.5 size-3.5 shrink-0', TONE[diagnostic.severity])} aria-label={diagnostic.severity} />
                <span className="w-14 shrink-0 font-mono text-muted-foreground">
                  {diagnostic.line}:{diagnostic.column}
                </span>
                <span className="min-w-0 flex-1 whitespace-pre-wrap break-words">{diagnostic.message}</span>
                <span className="hidden shrink-0 text-muted-foreground sm:inline">{diagnostic.source}</span>
              </li>
            )
          })}
        </ul>
      )}
    </section>
  )
}
