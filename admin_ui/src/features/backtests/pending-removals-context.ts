import { createContext, useContext } from 'react'

/** A run as its removal names it. */
export interface RunToRemove {
  id: string
  name: string
}

export interface PendingRemovals {
  /** The runs whose removal waits for its Undo, or was sent and is not read back yet: lists leave them out. */
  hidden: ReadonlySet<string>
  /** Hides the runs at once, and removes them once the Undo has run out. */
  remove: (runs: RunToRemove[]) => void
}

export const PendingRemovalsContext = createContext<PendingRemovals | null>(null)

const NONE: ReadonlySet<string> = new Set()

/** The runs a list leaves out; none outside the shell (a page tested alone). */
export function useHiddenRuns(): ReadonlySet<string> {
  return useContext(PendingRemovalsContext)?.hidden ?? NONE
}

export function useRemoveRuns(): (runs: RunToRemove[]) => void {
  const context = useContext(PendingRemovalsContext)
  if (!context) throw new Error('useRemoveRuns must be used inside <PendingRemovalsProvider>')
  return context.remove
}
