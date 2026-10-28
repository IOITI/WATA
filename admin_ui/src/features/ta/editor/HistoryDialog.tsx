// The versions of a script: each save of a changed source, never written again.
// One is shown beside the text being edited, and can be taken back into the editor
// (saving it then makes a new version: nothing is lost).

import { useQuery } from '@tanstack/react-query'
import { Button, cn, formatDateTime, SettleModal } from 'ioiti-ui'
import { useState } from 'react'

import { ClaudeBadge } from '@/components/status'

import { useSession } from '@/features/auth/auth-context'
import { api } from '@/lib/api'
import type { TaScriptSummary } from '@/lib/types'

import { ScriptDiff } from './ScriptDiff'

interface HistoryDialogProps {
  open: boolean
  onClose: () => void
  script: TaScriptSummary
  /** The text in the editor, set beside the version chosen. */
  current: string
  onRestore: (source: string, version: number) => void
}

// The screen less the dialog's margins, title, the labels of the two sides and its buttons.
const DIFF_HEIGHT = 'h-[calc(100dvh-17rem)] min-h-60'

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
                {entry.made_by === 'claude' && <ClaudeBadge className="ml-1.5 align-middle" />}
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
            <ScriptDiff className={DIFF_HEIGHT} before={versionQuery.data.source} after={current} />
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
