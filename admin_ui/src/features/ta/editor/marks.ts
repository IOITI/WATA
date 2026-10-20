// The checks' findings (diagnostics.ts) as CodeMirror's marks.

import type { Diagnostic as CodeMirrorDiagnostic } from '@codemirror/lint'
import type { Text } from '@codemirror/state'

import type { Diagnostic } from './diagnostics'

/** The checks' marks as CodeMirror places them: from a line and column to the end of the word or line. */
export function toCodeMirror(doc: Text, diagnostics: Diagnostic[]): CodeMirrorDiagnostic[] {
  return diagnostics.map((diagnostic) => {
    const line = doc.line(Math.min(Math.max(diagnostic.line, 1), doc.lines))
    const from = Math.min(line.to, line.from + Math.max(0, diagnostic.column - 1))
    const to = diagnostic.length ? Math.min(line.to, from + diagnostic.length) : line.to
    return { from, to: Math.max(to, from), severity: diagnostic.severity, message: diagnostic.message, source: diagnostic.source }
  })
}
