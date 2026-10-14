import { createContext, useContext, useLayoutEffect } from 'react'
import type { Dispatch, SetStateAction } from 'react'

import { trailFor } from './navigation'
import type { Crumb, PageName } from './navigation'

/** A page's name, with the address it was given for. */
export interface Named extends PageName {
  pathname: string
}

export interface Publisher {
  pathname: string
  publish: Dispatch<SetStateAction<Named | null>>
}

/** Where a page gives its name: the shell's (PageTrailProvider). */
export const PublisherContext = createContext<Publisher | null>(null)
/** The name the page shown gave, read by the top bar. */
export const NamedContext = createContext<Named | null>(null)

/**
 * Gives the trail of the top bar the name of the page and the pages between
 * its entry of the menu and itself. PageHeader does it with its title; outside
 * the shell (the tests of a page alone) it does nothing.
 */
export function usePageName(title: string, parents?: Crumb[]) {
  const publisher = useContext(PublisherContext)
  // Compared by value: a page builds its parents again at each render.
  const parentsKey = JSON.stringify(parents ?? [])
  // Before the page is painted, or its trail would show "…" for a frame.
  useLayoutEffect(() => {
    if (!publisher) return
    const named: Named = { pathname: publisher.pathname, title, parents: JSON.parse(parentsKey) as Crumb[] }
    publisher.publish(named)
    return () => publisher.publish((current) => (current === named ? null : current))
  }, [publisher, title, parentsKey])
}

/** The trail of the page shown, after Home. */
export function usePageTrail(pathname: string): Crumb[] {
  return trailFor(pathname, useContext(NamedContext))
}
