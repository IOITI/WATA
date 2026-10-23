import { createContext, useContext } from 'react'
import type { RefObject } from 'react'

/** The shell's <main>: the page scrolls in it, not in the window. What follows the scroll reads it here. */
export const MainScrollContext = createContext<RefObject<HTMLElement | null> | null>(null)

/** The element the page scrolls in; null outside the shell (the tests of a page alone). */
export function useMainScroll(): RefObject<HTMLElement | null> | null {
  return useContext(MainScrollContext)
}
