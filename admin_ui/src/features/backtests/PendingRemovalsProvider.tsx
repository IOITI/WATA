import { useQueryClient } from '@tanstack/react-query'
import { useCallback, useMemo, useRef, useState, type ReactNode } from 'react'

import { useToast } from '@/components/toast-context'
import { UndoToast } from '@/components/ui/undo-toast'
import { api } from '@/lib/api'

import { PendingRemovalsContext, type RunToRemove } from './pending-removals-context'

/** How long a removal waits for its Undo. */
export const UNDO_MS = 8_000

interface Removal {
  key: number
  runs: RunToRemove[]
}

function removedTitle(runs: RunToRemove[]): string {
  return runs.length === 1 ? `“${runs[0].name}” removed` : `${runs.length} backtests removed`
}

/**
 * Backtests are removed without a question first: they leave the lists at once, and the server is asked to delete
 * them once the Undo has run out (or the toast is closed). A removal asked while another waits sends the one
 * waiting. Nothing is sent before: a tab closed meanwhile deletes nothing. Lives in the shell, so that a run removed
 * from its own page goes on being removed on the list it leads back to.
 */
export function PendingRemovalsProvider({ children, duration = UNDO_MS }: { children: ReactNode; duration?: number }) {
  const queryClient = useQueryClient()
  const toast = useToast()
  const keys = useRef(0)
  // The removal whose Undo runs: a ref too, read by the callbacks of a toast already shown.
  const waiting = useRef<Removal | null>(null)
  const [pending, setPending] = useState<Removal | null>(null)
  const [sending, setSending] = useState<string[]>([])
  const [shown, setShown] = useState<Removal | null>(null)

  const send = useCallback(
    (removal: Removal) => {
      const ids = removal.runs.map((run) => run.id)
      setSending((current) => [...current, ...ids])
      api
        .removeBacktests(ids)
        .then((result) => {
          if (result.kept.length > 0) {
            const what = result.kept.length === 1 ? '1 backtest was kept' : `${result.kept.length} backtests were kept`
            toast({ title: what, description: result.kept[0].reason, variant: 'warning' })
          }
        })
        .catch((error: unknown) => {
          toast({
            title: 'The backtests could not be removed',
            description: error instanceof Error ? error.message : String(error),
            variant: 'error',
          })
        })
        .finally(async () => {
          // Shown again only once the list is read back: what was removed is then gone from it.
          await queryClient.invalidateQueries({ queryKey: ['backtests'] })
          setSending((current) => current.filter((id) => !ids.includes(id)))
        })
    },
    [queryClient, toast],
  )

  const remove = useCallback(
    (runs: RunToRemove[]) => {
      if (runs.length === 0) return
      if (waiting.current) send(waiting.current)
      keys.current += 1
      const removal = { key: keys.current, runs }
      waiting.current = removal
      setPending(removal)
      setShown(removal)
    },
    [send],
  )

  const undo = (removal: Removal) => {
    if (waiting.current?.key !== removal.key) return
    waiting.current = null
    setPending(null)
  }

  // The countdown ran out, or the toast was closed. Also called once an Undo has shown its message: nothing waits then.
  const expire = (removal: Removal) => {
    if (waiting.current?.key === removal.key) {
      waiting.current = null
      setPending(null)
      send(removal)
    }
    // Put away once its toast has gone out of sight.
    setTimeout(() => setShown((current) => (current?.key === removal.key ? null : current)), 400)
  }

  const hidden = useMemo(() => new Set([...(pending?.runs.map((run) => run.id) ?? []), ...sending]), [pending, sending])
  const value = useMemo(() => ({ hidden, remove }), [hidden, remove])

  return (
    <PendingRemovalsContext.Provider value={value}>
      {children}
      {shown && (
        <UndoToast
          key={shown.key}
          title={removedTitle(shown.runs)}
          // Short enough for the toast's one line.
          description="Deleted once the bar runs out"
          restoredMessage={shown.runs.length === 1 ? 'Kept: nothing was deleted' : 'Kept: none was deleted'}
          duration={duration}
          // Above the bar of a table's selection, where the other toasts are.
          wrapperClassName="bottom-20"
          onUndo={() => undo(shown)}
          onDismiss={() => expire(shown)}
        />
      )}
    </PendingRemovalsContext.Provider>
  )
}
