// The versions of a script: each save of a changed source, never written again.
// One is shown beside the text being edited, and can be taken back into the editor
// (saving it then makes a new version: nothing is lost).

import { syntaxHighlighting } from '@codemirror/language'
import { MergeView } from '@codemirror/merge'
import { EditorState } from '@codemirror/state'
import { EditorView } from '@codemirror/view'
import { useQuery } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { SettleModal } from '@/components/ui/settle-modal'
import { useSession } from '@/features/auth/auth-context'
import { api } from '@/lib/api'
import { formatDateTime } from '@/lib/format'
import type { TaScriptSummary } from '@/lib/types'
import { cn } from '@/lib/utils'

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
  pineLanguage,
  syntaxHighlighting(pineHighlight),
  EditorView.theme({
    '&': { fontSize: '12px', backgroundColor: 'var(--surface)', color: 'var(--text-primary)' },
    '.cm-scroller': { fontFamily: 'var(--font-mono)' },
    '.cm-changedLine': { backgroundColor: 'var(--accent-subtle)' },
  }),
]

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
  return <div ref={host} className="max-h-[55vh] overflow-auto rounded-lg border border-border" data-testid="version-diff" />
}

export function HistoryDialog({ open, onClose, script, current, onRestore }: HistoryDialogProps) {
  const { timezone } = useSession()
  const versions = [...script.versions].reverse()
  const [chosen, setChosen] = useState<number>(script.version)
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
      description="The version chosen (left) beside the text being edited (right)."
      size="xl"
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
        <ul className="flex max-h-[55vh] shrink-0 flex-col gap-1 overflow-auto md:w-48" aria-label="Versions">
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
        <div className="min-w-0 flex-1">
          {versionQuery.error && (
            <p role="alert" className="text-sm text-negative">
              {versionQuery.error.message}
            </p>
          )}
          {versionQuery.data &&
            (versionQuery.data.source === current ? (
              <p className="text-sm text-muted-foreground">Version {chosen} is the text in the editor.</p>
            ) : (
              <Diff before={versionQuery.data.source} after={current} />
            ))}
        </div>
      </div>
    </SettleModal>
  )
}
