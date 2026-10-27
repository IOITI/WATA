// The versions of a script: each save of a changed source, never written again.
// One is shown beside the text being edited, and can be taken back into the editor
// (saving it then makes a new version: nothing is lost).

import { syntaxHighlighting } from '@codemirror/language'
import { MergeView } from '@codemirror/merge'
import { EditorState } from '@codemirror/state'
import { EditorView, lineNumbers } from '@codemirror/view'
import { useQuery } from '@tanstack/react-query'
import { Button, cn, formatDateTime, SettleModal } from 'ioiti-ui'
import { useEffect, useRef, useState } from 'react'

import { useSession } from '@/features/auth/auth-context'
import { api } from '@/lib/api'
import type { TaScriptSummary } from '@/lib/types'

import { pineHighlight, pineLanguage } from './pineLanguage'

interface HistoryDialogProps {
  open: boolean
  onClose: () => void
  script: TaScriptSummary
  /** The text in the editor, set beside the version chosen. */
  current: string
  onRestore: (source: string, version: number) => void
}

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

// The screen less the dialog's margins, title, the labels of the two sides and its buttons.
const DIFF_HEIGHT = 'h-[calc(100dvh-17rem)] min-h-60'

function Diff({ before, after }: { before: string; after: string }) {
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
  return <div ref={host} className={cn('overflow-auto rounded-lg border border-border', DIFF_HEIGHT)} data-testid="version-diff" />
}

export function HistoryDialog({ open, onClose, script, current, onRestore }: HistoryDialogProps) {
  const { timezone } = useSession()
  const versions = [...script.versions].reverse()
  const [chosen, setChosen] = useState<number>(script.version)
  const chosenEntry = script.versions.find((entry) => entry.number === chosen)
  const versionQuery = useQuery({
    queryKey: ['ta-script-version', script.id, chosen],
    queryFn: () => api.taScriptVersion(script.id, chosen),
    enabled: open,
    staleTime: Infinity,
  })

  return (
    <SettleModal
      open={open}
      onClose={onClose}
      title={`History of ${script.name}`}
      description="The version chosen in the list, beside the text being edited."
      size="full"
      footer={
        <>
          <Button variant="ghost" size="sm" onClick={onClose}>
            Close
          </Button>
          <Button
            size="sm"
            disabled={!versionQuery.data || versionQuery.data.source === current}
            onClick={() => {
              if (versionQuery.data) onRestore(versionQuery.data.source, versionQuery.data.number)
            }}
          >
            Put version {chosen} in the editor
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3 md:flex-row">
        <ul className="flex max-h-40 shrink-0 flex-col gap-1 overflow-auto md:max-h-[calc(100dvh-15rem)] md:w-48" aria-label="Versions">
          {versions.map((entry) => (
            <li key={entry.number}>
              <button
                type="button"
                className={cn(
                  'w-full rounded-lg px-2 py-1.5 text-left text-xs hover:bg-surface-hover',
                  entry.number === chosen && 'bg-surface-raised font-medium',
                )}
                aria-pressed={entry.number === chosen}
                onClick={() => setChosen(entry.number)}
              >
                <span className="font-mono">v{entry.number}</span>
                {entry.number === script.version && <span className="text-muted-foreground"> · saved last</span>}
                <span className="block text-muted-foreground">{formatDateTime(entry.saved_at, timezone, false)}</span>
              </button>
            </li>
          ))}
        </ul>
        {/* The same height whatever is shown, so that choosing a version does not move the dialog. */}
        <div className="flex min-w-0 flex-1 flex-col gap-1.5">
          {/* Over the two halves of the comparison, which side is which. */}
          <div className="grid grid-cols-2 gap-3 text-xs">
            <p>
              <span className="font-medium">Version {chosen}</span>
              {chosenEntry && (
                <span className="text-muted-foreground"> · saved {formatDateTime(chosenEntry.saved_at, timezone, false)}</span>
              )}
            </p>
            <p className="font-medium">In the editor</p>
          </div>
          {versionQuery.error ? (
            <p role="alert" className={cn('text-sm text-negative', DIFF_HEIGHT)}>
              {versionQuery.error.message}
            </p>
          ) : versionQuery.data && versionQuery.data.source !== current ? (
            <Diff before={versionQuery.data.source} after={current} />
          ) : (
            <div className={cn('grid place-items-center rounded-lg border border-border text-sm text-muted-foreground', DIFF_HEIGHT)}>
              {versionQuery.data && `Version ${chosen} is the text in the editor.`}
            </div>
          )}
        </div>
      </div>
    </SettleModal>
  )
}
