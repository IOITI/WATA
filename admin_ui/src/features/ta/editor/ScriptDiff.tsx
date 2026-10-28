// Two versions of a Pine script side by side, their unchanged lines folded: the history of a script, and the
// AI agent's proposal of a new version beside the one it was made on.

import { syntaxHighlighting } from '@codemirror/language'
import { MergeView } from '@codemirror/merge'
import { EditorState } from '@codemirror/state'
import { EditorView, lineNumbers } from '@codemirror/view'
import { cn } from 'ioiti-ui'
import { useEffect, useRef } from 'react'

import { pineHighlight, pineLanguage } from './pineLanguage'

const readOnly = [
  EditorState.readOnly.of(true),
  EditorView.editable.of(false),
  // Each side as wide as half the dialog: a long line wraps instead of hiding its end.
  EditorView.lineWrapping,
  lineNumbers(),
  pineLanguage,
  syntaxHighlighting(pineHighlight),
  EditorView.theme({
    '&': { fontSize: '12px', backgroundColor: 'var(--surface)', color: 'var(--text-primary)' },
    '.cm-scroller': { fontFamily: 'var(--font-mono)' },
    // CodeMirror's own is a light grey whatever the theme. The side's colour, not the editor's darker one:
    // it stops where the text does, above the room the two sides are given.
    '.cm-gutters': { backgroundColor: 'var(--surface)', color: 'var(--text-muted)', borderRight: '1px solid var(--border)' },
    '.cm-changedLine': { backgroundColor: 'var(--accent-subtle)' },
  }),
]

/** ``className``: its height (the two sides scroll together in it). */
export function ScriptDiff({ before, after, className }: { before: string; after: string; className?: string }) {
  const host = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!host.current) return
    const view = new MergeView({
      a: { doc: before, extensions: readOnly },
      b: { doc: after, extensions: readOnly },
      parent: host.current,
      collapseUnchanged: { margin: 3, minSize: 6 },
    })
    return () => view.destroy()
  }, [before, after])
  // As tall as the dialog leaves room for: the two sides scroll together.
  return <div ref={host} className={cn('overflow-auto rounded-lg border border-border', className)} data-testid="version-diff" />
}
